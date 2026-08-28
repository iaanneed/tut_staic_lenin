from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import compute_view_bearing
from scripts.build_sync_pr_body import build_body
from scripts.compute_view_bearing import target_feature_indices
from scripts.fetch_osm_lenins import (
    build_overpass_query,
    elements_to_geojson,
    fetch_overpass_elements,
)
from scripts.fetch_telegram_channel import latest_source_id, telegram_export_message
from scripts.fetch_osm_lenins import dumps_geojson, write_geojson
from scripts.parse_new_monuments import ingest_export, parse_message
from scripts.validate_data import validate

FIXTURE = Path(__file__).parent / "fixtures" / "telegram_messages.json"


def point_feature(coordinates, properties=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties or {},
    }


def test_telegram_adapter_matches_export_schema():
    message = telegram_export_message(
        123,
        "2026-08-01T12:00:00+00:00",
        "г. Мінск",
        "photos/photo.jpg",
    )
    assert parse_message(message)["source_id"] == 123
    assert parse_message(message)["city"] == "г. Мінск"


def test_parse_message_accepts_village_prefix():
    message = telegram_export_message(
        200,
        "2026-08-01T12:00:00+00:00",
        "Помнік каля школы\nв. Заброддзе\n#Мінская\n📍 53.9000, 27.5667",
        "photos/photo.jpg",
    )
    parsed = parse_message(message)
    assert parsed["city"] == "в. Заброддзе"
    assert parsed["title"] == "Помнік каля школы"
    assert parsed["coordinates"] == [27.5667, 53.9]


def test_village_prefix_does_not_eat_lenin_initials():
    message = telegram_export_message(
        201,
        "2026-08-01T12:00:00+00:00",
        "В. І. Ленін на сваім сходзе\nг. Смалявічы\n#Мінская\n📍 54.0000, 28.0000",
        "photos/photo.jpg",
    )
    parsed = parse_message(message)
    assert parsed["city"] == "г. Смалявічы"
    assert parsed["title"] == "В. І. Ленін на сваім сходзе"


def test_latest_source_id_uses_committed_monuments(tmp_path):
    path = tmp_path / "monuments.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    point_feature([27, 53], {"source_id": 4}),
                    point_feature([28, 54], {"source_id": 9}),
                ],
            }
        ),
        encoding="utf-8",
    )
    assert latest_source_id(path) == 9


def test_ingest_export_is_incremental_and_requires_coordinates(tmp_path):
    export_dir = tmp_path / "export"
    photos_dir = export_dir / "photos"
    photos_dir.mkdir(parents=True)
    shutil.copy(FIXTURE, export_dir / "result.json")
    (photos_dir / "photo_100.jpg").write_bytes(b"photo")
    (photos_dir / "photo_101.jpg").write_bytes(b"photo")

    monuments_path = tmp_path / "monuments.geojson"
    monuments_path.write_text(
        '{"type":"FeatureCollection","features":[]}',
        encoding="utf-8",
    )
    target_photos = tmp_path / "photos"

    added = ingest_export(
        export_dir,
        require_coordinates=True,
        monuments_path=monuments_path,
        target_photos_dir=target_photos,
    )
    assert added == [100]
    assert (target_photos / "photo_100.jpg").is_file()

    assert (
        ingest_export(
            export_dir,
            require_coordinates=True,
            monuments_path=monuments_path,
            target_photos_dir=target_photos,
        )
        == []
    )


def test_bearing_targets_only_features_without_property():
    features = [
        point_feature([27, 53], {"viewBearing": 90}),
        point_feature([28, 54], {"viewBearing": None}),
        point_feature([29, 55], {}),
    ]
    assert target_feature_indices(features) == [2]
    assert target_feature_indices(features, force=True) == [0, 1, 2]


def test_overpass_failure_is_not_treated_as_empty_result(monkeypatch):
    def fail_request(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(compute_view_bearing.requests, "post", fail_request)
    monkeypatch.setattr(compute_view_bearing.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="Overpass API failed"):
        compute_view_bearing.overpass_query('way["highway"];')


def test_osm_lenin_query_uses_api_compatible_area():
    query = build_overpass_query()
    assert 'area["ISO3166-1"="BY"][admin_level=2]->.searchArea;' in query
    assert "{{geocodeArea" not in query
    assert 'nwr["historic"~"^(memorial|monument)$"]' in query
    assert "subject:wikidata" not in query
    assert "out center;" in query


def test_elements_to_geojson_keeps_nodes_and_way_centers():
    elements = [
        {
            "type": "node",
            "id": 123,
            "lat": 53.9,
            "lon": 27.5,
            "tags": {"name": "Ленін", "historic": "memorial", "memorial": "statue"},
        },
        {
            "type": "way",
            "id": 9,
            "center": {"lat": 52.1, "lon": 29.2},
            "tags": {
                "historic": "memorial",
                "name": "У.І.Ленін",
                "name:be": "У.І.Ленін",
                "name:ru": "В.И.Ленин",
            },
        },
        {"type": "way", "id": 10, "tags": {"name": "no center"}},
        {"type": "node", "id": 456, "tags": {"name": "no coords"}},
    ]
    collection = elements_to_geojson(elements)
    assert len(collection["features"]) == 2
    node, way = collection["features"]
    assert node["geometry"]["coordinates"] == [27.5, 53.9]
    assert node["properties"]["@id"] == "node/123"
    assert node["properties"]["name"] == "Ленін"
    assert way["geometry"]["coordinates"] == [29.2, 52.1]
    assert way["properties"]["@id"] == "way/9"
    assert way["properties"]["name"] == "У.І.Ленін"


def test_fetch_osm_lenins_retries_then_fails(monkeypatch):
    from scripts import fetch_osm_lenins

    def fail_request(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(fetch_osm_lenins.requests, "post", fail_request)
    monkeypatch.setattr(fetch_osm_lenins.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="Overpass API failed"):
        fetch_overpass_elements("query", attempts=3, sleep=0)


def test_validate_detects_duplicate_ids_and_missing_photo(tmp_path):
    monuments = tmp_path / "monuments.geojson"
    possible = tmp_path / "possible.geojson"
    monuments.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    point_feature(
                        [27, 53],
                        {
                            "source_id": 1,
                            "imageUrl_preview": "photos/missing.jpg",
                            "imageUrl_full": "photos/missing.jpg",
                        },
                    ),
                    point_feature(
                        [28, 54],
                        {
                            "source_id": 1,
                            "imageUrl_preview": "photos/missing.jpg",
                            "imageUrl_full": "photos/missing.jpg",
                        },
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    possible.write_text(
        '{"type":"FeatureCollection","features":[]}',
        encoding="utf-8",
    )
    errors = validate(monuments, possible, tmp_path)
    assert any("duplicate source_id" in error for error in errors)
    assert any("photo does not exist" in error for error in errors)


def test_pr_body_lists_actual_added_and_removed_features():
    monuments_before = {
        "type": "FeatureCollection",
        "features": [point_feature([27, 53], {"source_id": 1, "city": "г. Стары"})],
    }
    monuments_after = {
        "type": "FeatureCollection",
        "features": [
            point_feature(
                [27, 53],
                {"source_id": 1, "city": "г. Стары", "viewBearing": 90},
            ),
            point_feature(
                [28, 54],
                {"source_id": 2, "city": "г. Новы", "title": "Помнік"},
            ),
        ],
    }
    possible_before = {
        "type": "FeatureCollection",
        "features": [point_feature([28, 54], {"id": "node/2", "name": "Ленін"})],
    }
    possible_after = {
        "type": "FeatureCollection",
        "features": [point_feature([29, 55], {"id": "node/7", "name": "Ільіч"})],
    }

    body = build_body(
        monuments_before,
        monuments_after,
        possible_before,
        possible_after,
        [2],
    )

    assert "Telegram #2: г. Новы — Помнік" in body
    assert "[node/2](https://www.openstreetmap.org/node/2): Ленін (54, 28)" in body
    assert "[node/7](https://www.openstreetmap.org/node/7): Ільіч (55, 29)" in body
    assert "Possible layer: 1 → 1 (+1 / −1)" in body
    assert "Telegram #1: г. Стары — 90°" in body


def test_pr_body_truncates_long_possible_lists():
    possible_after = {
        "type": "FeatureCollection",
        "features": [
            point_feature([27, 53], {"id": f"node/{index}", "name": "Ленін"})
            for index in range(40)
        ],
    }
    empty = {"type": "FeatureCollection", "features": []}

    body = build_body(empty, empty, empty, possible_after, [])

    assert "- …and 15 more." in body


def test_generated_geojson_is_sorted_and_line_diffable(tmp_path):
    collection = {
        "type": "FeatureCollection",
        "features": [
            point_feature([27, 53], {"id": "node/20"}),
            point_feature([28, 54], {"id": "way/3"}),
            point_feature([29, 55], {"id": "node/3"}),
        ],
    }
    path = tmp_path / "possible.geojson"
    write_geojson(path, collection)

    text = path.read_text(encoding="utf-8")
    written = json.loads(text)
    assert [f["properties"]["id"] for f in written["features"]] == [
        "node/3",
        "node/20",
        "way/3",
    ]
    assert text.endswith("\n")
    assert text.count("\n") > len(collection["features"])

    reversed_collection = {
        "type": "FeatureCollection",
        "features": list(reversed(collection["features"])),
    }
    assert dumps_geojson(reversed_collection) == text

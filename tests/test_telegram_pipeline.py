from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import compute_view_bearing
from scripts.build_sync_pr_body import build_body
from scripts.compare_geojson import apply_existing_cities, existing_cities
from scripts.compute_view_bearing import target_feature_indices
from scripts.fetch_osm_lenins import (
    build_overpass_query,
    elements_to_geojson,
    fetch_overpass_elements,
)
from scripts.fetch_telegram_channel import latest_source_id, telegram_export_message
from scripts.fetch_osm_lenins import dumps_geojson, write_geojson
from scripts.parse_new_monuments import ingest_export, parse_message
from scripts.reverse_geocode_cities import (
    format_city,
    geocode_collection,
    reverse_lookup,
    target_feature_indices as city_target_indices,
)
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


def test_ingest_prompts_for_missing_coordinates(tmp_path):
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
    prompts: list[str] = []

    def input_fn(message: str) -> str:
        prompts.append(message)
        return "53.9, 27.5"

    added = ingest_export(
        export_dir,
        monuments_path=monuments_path,
        target_photos_dir=target_photos,
        input_fn=input_fn,
    )
    assert added == [100, 101]
    assert any("Telegram #101" in message for message in prompts)

    features = json.loads(monuments_path.read_text(encoding="utf-8"))["features"]
    by_id = {f["properties"]["source_id"]: f for f in features}
    assert by_id[101]["geometry"]["coordinates"] == [27.5, 53.9]


def test_ingest_skips_when_coordinate_prompt_is_empty(tmp_path):
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

    added = ingest_export(
        export_dir,
        monuments_path=monuments_path,
        target_photos_dir=tmp_path / "photos",
        input_fn=lambda _message: "",
    )
    assert added == [100]


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


def test_validate_requires_city_on_possible_features(tmp_path):
    monuments = tmp_path / "monuments.geojson"
    possible = tmp_path / "possible.geojson"
    monuments.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    possible.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [point_feature([27, 53], {"id": "node/1", "name": "Ленін"})],
            }
        ),
        encoding="utf-8",
    )
    errors = validate(monuments, possible, tmp_path)
    assert any("missing city" in error for error in errors)


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


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_format_city_uses_closest_settlement_and_belarusian_prefixes():
    assert format_city({"city": "Мінск"}) == "г. Мінск"
    assert format_city({"town": "Радашковічы"}) == "г.п. Радашковічы"
    assert format_city({"village": "Заброддзе", "city": "Мінск"}) == "в. Заброддзе"
    assert format_city({"hamlet": "Хутар"}) == "в. Хутар"
    assert format_city({"municipality": "Смаргонскі сельсавет"}) == "Смаргонскі сельсавет"
    assert format_city({}) is None
    assert (
        format_city({"town": "Ганцавічы"}, {"name:prefix:be": "горад"})
        == "г. Ганцавічы"
    )
    assert (
        format_city({"town": "Старобін"}, {"name:prefix:be": "гарадскі пасёлак"})
        == "г.п. Старобін"
    )


def test_geocode_skips_features_that_already_have_city():
    collection = {
        "type": "FeatureCollection",
        "features": [
            point_feature([27.5, 53.9], {"id": "node/1", "name": "Ленін", "city": "г. Мінск"}),
            point_feature([29.2, 52.1], {"id": "node/2", "name": "Ленін"}),
        ],
    }
    calls: list[tuple] = []

    def get_fn(url, params, headers, timeout):
        calls.append((url, params))
        return FakeResponse({"address": {"city": "Гомель"}})

    logs: list[str] = []
    failed = geocode_collection(
        collection,
        min_interval=0,
        get_fn=get_fn,
        sleep_fn=lambda _seconds: None,
        log_fn=logs.append,
    )
    assert failed == []
    assert city_target_indices(collection["features"]) == []
    assert collection["features"][1]["properties"]["city"] == "г. Гомель"
    assert len(calls) == 1
    assert calls[0][1]["lat"] == 52.1
    assert calls[0][1]["lon"] == 29.2
    assert any("geocoded 1/1  node/2  г. Гомель" in line for line in logs)
    assert calls[0][1]["extratags"] == 1
    assert calls[0][1]["zoom"] == 18


def test_geocode_looks_up_name_prefix_for_towns():
    collection = {
        "type": "FeatureCollection",
        "features": [
            point_feature([26.4274448, 52.7624215], {"id": "node/1", "name": "Ленін"}),
        ],
    }
    zooms: list[int] = []

    def get_fn(url, params, headers, timeout):
        zooms.append(params["zoom"])
        if params["zoom"] == 18:
            return FakeResponse(
                {
                    "address": {"town": "Ганцавічы"},
                    "extratags": {"memorial": "statue"},
                }
            )
        return FakeResponse(
            {
                "address": {"town": "Ганцавічы"},
                "extratags": {"place": "town", "name:prefix:be": "горад"},
            }
        )

    failed = geocode_collection(
        collection,
        min_interval=0,
        get_fn=get_fn,
        sleep_fn=lambda _seconds: None,
        log_fn=lambda _line: None,
    )
    assert failed == []
    assert zooms == [18, 12]
    assert collection["features"][0]["properties"]["city"] == "г. Ганцавічы"


def test_geocode_does_not_look_up_prefix_for_villages():
    collection = {
        "type": "FeatureCollection",
        "features": [
            point_feature([30.94, 52.50], {"id": "node/1", "name": "Ленін"}),
        ],
    }
    zooms: list[int] = []

    def get_fn(url, params, headers, timeout):
        zooms.append(params["zoom"])
        return FakeResponse(
            {
                "address": {"village": "Яроміна", "city": "Яромінскі сельскі Савет"},
                "extratags": {"memorial": "statue"},
            }
        )

    geocode_collection(
        collection,
        min_interval=0,
        get_fn=get_fn,
        sleep_fn=lambda _seconds: None,
        log_fn=lambda _line: None,
    )
    assert zooms == [18]
    assert collection["features"][0]["properties"]["city"] == "в. Яроміна"


def test_reverse_lookup_retries_on_rate_limit():
    attempts = {"count": 0}

    def get_fn(url, params, headers, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return FakeResponse({}, status_code=429)
        return FakeResponse({"address": {"village": "Іўе"}})

    payload = reverse_lookup(
        53.9,
        27.5,
        get_fn=get_fn,
        sleep_fn=lambda _seconds: None,
        sleep=0,
    )
    assert payload["address"]["village"] == "Іўе"
    assert attempts["count"] == 2


def test_compare_reuses_cached_cities_by_osm_id(tmp_path):
    previous = tmp_path / "possible_lenin.geojson"
    previous.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    point_feature(
                        [27, 53],
                        {"id": "node/1", "name": "Ленін", "city": "г. Мінск"},
                    ),
                    point_feature(
                        [28, 54],
                        {"id": "node/2", "name": "Ленін", "city": "г.п. Радашковічы"},
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    rebuilt = {
        "type": "FeatureCollection",
        "features": [
            point_feature([27, 53], {"id": "node/1", "name": "Ленін", "memorial": "statue"}),
            point_feature([29, 55], {"id": "node/9", "name": "Ленін"}),
        ],
    }
    apply_existing_cities(rebuilt, existing_cities(previous))
    by_id = {f["properties"]["id"]: f["properties"] for f in rebuilt["features"]}
    assert by_id["node/1"]["city"] == "г. Мінск"
    assert "city" not in by_id["node/9"]
    assert by_id["node/1"]["memorial"] == "statue"

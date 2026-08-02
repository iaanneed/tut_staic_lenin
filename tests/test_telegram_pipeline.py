from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import compute_view_bearing
from scripts.compute_view_bearing import target_feature_indices
from scripts.fetch_telegram_channel import latest_source_id, telegram_export_message
from scripts.parse_new_monuments import ingest_export, parse_message
from scripts.prune_possible import prune_features
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


def test_prune_possible_uses_source_specific_radius():
    monuments = {
        "type": "FeatureCollection",
        "features": [point_feature([27.0, 53.0])],
    }
    possible = {
        "type": "FeatureCollection",
        "features": [
            point_feature([27.0001, 53.0], {"id": "node/1"}),
            point_feature([27.005, 53.0], {"source": "3dparty"}),
            point_feature([27.02, 53.0], {"id": "node/2"}),
        ],
    }
    output, removed = prune_features(possible, monuments)
    assert removed == 2
    assert [feature["properties"]["id"] for feature in output["features"]] == [
        "node/2"
    ]


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

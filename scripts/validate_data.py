#!/usr/bin/env python3
"""Validate committed GeoJSON data and monument photo references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MONUMENTS = PROJECT_ROOT / "monuments.geojson"
DEFAULT_POSSIBLE = PROJECT_ROOT / "possible_lenin.geojson"


def load_feature_collection(path: Path, errors: list[str]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return {"features": []}

    if data.get("type") != "FeatureCollection" or not isinstance(
        data.get("features"), list
    ):
        errors.append(f"{path}: expected a GeoJSON FeatureCollection")
        return {"features": []}
    return data


def valid_point(feature: dict) -> bool:
    geometry = feature.get("geometry", {})
    coordinates = geometry.get("coordinates")
    return (
        geometry.get("type") == "Point"
        and isinstance(coordinates, list)
        and len(coordinates) >= 2
        and all(isinstance(value, (int, float)) for value in coordinates[:2])
        and -180 <= coordinates[0] <= 180
        and -90 <= coordinates[1] <= 90
        and coordinates[:2] != [0, 0]
    )


def validate(
    monuments_path: Path,
    possible_path: Path,
    project_root: Path,
) -> list[str]:
    errors: list[str] = []
    monuments = load_feature_collection(monuments_path, errors)
    possible = load_feature_collection(possible_path, errors)

    source_ids: set[int] = set()
    for index, feature in enumerate(monuments.get("features", [])):
        prefix = f"{monuments_path}: feature {index}"
        if not valid_point(feature):
            errors.append(f"{prefix}: invalid Point coordinates")

        properties = feature.get("properties", {})
        source_id = properties.get("source_id")
        if not isinstance(source_id, int):
            errors.append(f"{prefix}: source_id must be an integer")
        elif source_id in source_ids:
            errors.append(f"{prefix}: duplicate source_id {source_id}")
        else:
            source_ids.add(source_id)

        for field in ("imageUrl_preview", "imageUrl_full"):
            photo = properties.get(field)
            if not isinstance(photo, str) or not photo:
                errors.append(f"{prefix}: missing {field}")
            elif not (project_root / photo).is_file():
                errors.append(f"{prefix}: photo does not exist: {photo}")

    for index, feature in enumerate(possible.get("features", [])):
        if not valid_point(feature):
            errors.append(f"{possible_path}: feature {index}: invalid Point coordinates")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monuments", type=Path, default=DEFAULT_MONUMENTS)
    parser.add_argument("--possible", type=Path, default=DEFAULT_POSSIBLE)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    errors = validate(args.monuments, args.possible, args.project_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("GeoJSON and photo references are valid")


if __name__ == "__main__":
    main()

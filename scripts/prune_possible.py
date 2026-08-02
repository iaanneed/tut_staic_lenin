#!/usr/bin/env python3
"""Remove possible Lenin points that are already confirmed monuments."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MONUMENTS = PROJECT_ROOT / "monuments.geojson"
DEFAULT_POSSIBLE = PROJECT_ROOT / "possible_lenin.geojson"

OSM_RADIUS_M = 20
PARTY3D_RADIUS_M = 1000


def haversine_m(first: list[float], second: list[float]) -> float:
    lon1, lat1 = first
    lon2, lat2 = second
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def point_coordinates(feature: dict) -> list[float] | None:
    geometry = feature.get("geometry", {})
    coordinates = geometry.get("coordinates")
    if (
        geometry.get("type") != "Point"
        or not isinstance(coordinates, list)
        or len(coordinates) < 2
    ):
        return None
    return coordinates[:2]


def candidate_radius(feature: dict) -> int:
    source = str(feature.get("properties", {}).get("source", "")).lower()
    return PARTY3D_RADIUS_M if "3dparty" in source else OSM_RADIUS_M


def prune_features(possible: dict, monuments: dict) -> tuple[dict, int]:
    monument_points = [
        coordinates
        for feature in monuments.get("features", [])
        if (coordinates := point_coordinates(feature)) is not None
    ]

    kept = []
    removed = 0
    for feature in possible.get("features", []):
        coordinates = point_coordinates(feature)
        radius = candidate_radius(feature)
        is_confirmed = coordinates is not None and any(
            haversine_m(coordinates, monument) <= radius
            for monument in monument_points
        )
        if is_confirmed:
            removed += 1
        else:
            kept.append(feature)

    return {"type": "FeatureCollection", "features": kept}, removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monuments", type=Path, default=DEFAULT_MONUMENTS)
    parser.add_argument("--possible", type=Path, default=DEFAULT_POSSIBLE)
    args = parser.parse_args()

    try:
        monuments = json.loads(args.monuments.read_text(encoding="utf-8"))
        possible = json.loads(args.possible.read_text(encoding="utf-8"))
        output, removed = prune_features(possible, monuments)
        args.possible.write_text(
            json.dumps(output, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Removed {removed} confirmed points from {args.possible}")


if __name__ == "__main__":
    main()

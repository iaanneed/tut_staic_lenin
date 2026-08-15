#!/usr/bin/env python3
"""Fetch Belarus Lenin memorials from Overpass into scripts/raw_data/osm_lenin.geojson."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "scripts" / "raw_data"
DEFAULT_OUTPUT = RAW_DATA_DIR / "osm_lenin.geojson"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {
    "User-Agent": "tut-staic-lenin-osm-fetch/1.0 (contact: ivan.liadzian@mapbox.com)"
}


OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="BY"][admin_level=2]->.searchArea;
(
  nwr["historic"~"^(memorial|monument)$"]["name"~"Ленін|Ленин|Lenin",i](area.searchArea);
  nwr["historic"~"^(memorial|monument)$"]["name:ru"~"Ленин",i](area.searchArea);
  nwr["historic"~"^(memorial|monument)$"]["name:be"~"Ленін",i](area.searchArea);
);
out center;
""".strip()


def build_overpass_query() -> str:
    return OVERPASS_QUERY


def element_coordinates(element: dict) -> tuple[float, float] | None:
    """Return lon/lat for a node, or the Overpass center for a way/relation."""
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return float(lon), float(lat)


def elements_to_geojson(elements: list[dict]) -> dict:
    """Convert Overpass elements into Point features (polygons → center point)."""
    features = []
    for element in elements:
        osm_type = element.get("type")
        if osm_type not in {"node", "way", "relation"}:
            continue

        coordinates = element_coordinates(element)
        if coordinates is None:
            continue

        osm_id = element.get("id")
        tags = element.get("tags") or {}
        properties = {"@id": f"{osm_type}/{osm_id}", **tags}
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [coordinates[0], coordinates[1]],
                },
                "properties": properties,
            }
        )

    return {"type": "FeatureCollection", "features": features}


def fetch_overpass_elements(
    query: str,
    *,
    url: str = OVERPASS_URL,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
    attempts: int = 3,
    sleep: float = 5.0,
) -> list[dict]:
    last_error: Exception | None = None
    request_headers = headers if headers is not None else HEADERS
    for attempt in range(attempts):
        try:
            response = requests.post(
                url,
                data={"data": query},
                headers=request_headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            elements = payload.get("elements")
            if not isinstance(elements, list):
                raise RuntimeError("Overpass response missing elements list")
            return elements
        except Exception as exc:
            last_error = exc
            print(f"  overpass error (attempt {attempt + 1}): {exc}", file=sys.stderr)
            if attempt + 1 < attempts:
                time.sleep(sleep)
    raise RuntimeError("Overpass API failed after 3 attempts") from last_error


def write_geojson(path: Path, collection: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(collection, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    query = build_overpass_query()
    print("Fetching OSM Lenins from Overpass...", file=sys.stderr)
    elements = fetch_overpass_elements(query)
    collection = elements_to_geojson(elements)
    write_geojson(args.output, collection)
    print(f"Wrote {len(collection['features'])} features to {args.output}")


if __name__ == "__main__":
    main()

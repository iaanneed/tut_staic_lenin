#!/usr/bin/env python3
"""Fill missing `city` properties in possible_lenin.geojson via Nominatim reverse.

Public Nominatim allows at most 1 request per second. This script is sequential,
identifies itself with a User-Agent, caches results in the GeoJSON itself, and
skips features that already have a non-empty city.

`address.town` is not enough to tell a Belarusian city from an urban settlement:
both are OSM `place=town`. The official type is `name:prefix` on the settlement
object. A zoom=18 reverse hits the memorial, so towns get a second reverse at
zoom=12 with `extratags=1`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import requests

try:
    from scripts.fetch_osm_lenins import write_geojson
except ImportError:
    from fetch_osm_lenins import write_geojson

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GEOJSON = PROJECT_ROOT / "possible_lenin.geojson"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org"
MIN_INTERVAL_SECONDS = 1.1
HEADERS = {
    "User-Agent": "tut-staic-lenin-reverse/1.0 (contact: ivan.liadzian@mapbox.com)",
    "Accept-Language": "be",
}

# Closest settlement first. Prefix for `town` comes from OSM name:prefix when possible.
SETTLEMENT_KEYS = (
    ("isolated_dwelling", "в."),
    ("farm", "в."),
    ("allotments", "в."),
    ("hamlet", "в."),
    ("village", "в."),
    ("town", "г.п."),
    ("city", "г."),
)
PREFIX_BY_NAME = {
    "горад": "г.",
    "город": "г.",
    "гарадскі пасёлак": "г.п.",
    "городской посёлок": "г.п.",
    "пасёлак гарадскога тыпу": "г.п.",
    "поселок городского типа": "г.п.",
    "вёска": "в.",
    "деревня": "в.",
    "аграгарадок": "аг.",
    "агрогородок": "аг.",
    "пасёлак": "п.",
    "посёлок": "п.",
    "поселок": "п.",
}


def nominatim_base_url() -> str:
    return os.environ.get("NOMINATIM_URL", DEFAULT_NOMINATIM_URL).rstrip("/")


def feature_id(feature: dict) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("id") or properties.get("@id") or "")


def city_value(feature: dict) -> str | None:
    properties = feature.get("properties") or {}
    city = properties.get("city")
    if isinstance(city, str) and city.strip():
        return city.strip()
    return None


def target_feature_indices(features: list[dict], force: bool = False) -> list[int]:
    if force:
        return list(range(len(features)))
    return [
        index
        for index, feature in enumerate(features)
        if city_value(feature) is None
    ]


def closest_settlement(address: dict | None) -> tuple[str, str] | None:
    if not isinstance(address, dict):
        return None
    for key, _prefix in SETTLEMENT_KEYS:
        name = address.get(key)
        if isinstance(name, str) and name.strip():
            return key, name.strip()
    return None


def prefix_from_extratags(extratags: dict | None) -> str | None:
    """Official Belarusian settlement type lives on the place object, not in address."""
    if not isinstance(extratags, dict):
        return None
    for key in ("name:prefix:be", "name:prefix", "name:prefix:ru"):
        raw = extratags.get(key)
        if isinstance(raw, str) and raw.strip():
            mapped = PREFIX_BY_NAME.get(raw.strip().casefold())
            if mapped:
                return mapped
    place = extratags.get("linked_place") or extratags.get("place")
    if place == "city":
        return "г."
    return None


def format_city(address: dict | None, extratags: dict | None = None) -> str | None:
    settlement = closest_settlement(address)
    if settlement is None:
        municipality = address.get("municipality") if isinstance(address, dict) else None
        if isinstance(municipality, str) and municipality.strip():
            return municipality.strip()
        return None
    key, name = settlement
    default_prefix = dict(SETTLEMENT_KEYS)[key]
    prefix = prefix_from_extratags(extratags) or default_prefix
    return f"{prefix} {name}"


def needs_town_prefix_lookup(address: dict | None, extratags: dict | None) -> bool:
    settlement = closest_settlement(address)
    return (
        settlement is not None
        and settlement[0] == "town"
        and prefix_from_extratags(extratags) is None
    )


def point_lat_lon(feature: dict) -> tuple[float, float] | None:
    coordinates = (feature.get("geometry") or {}).get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or not isinstance(coordinates[0], (int, float))
        or not isinstance(coordinates[1], (int, float))
    ):
        return None
    return float(coordinates[1]), float(coordinates[0])


def reverse_lookup(
    lat: float,
    lon: float,
    *,
    zoom: int = 18,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    attempts: int = 3,
    sleep: float = 2.0,
    get_fn: Callable[..., requests.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    url = f"{(base_url or nominatim_base_url()).rstrip('/')}/reverse"
    request_headers = headers if headers is not None else HEADERS
    getter = get_fn or requests.get
    last_error: Exception | None = None
    delay = sleep
    for attempt in range(attempts):
        try:
            response = getter(
                url,
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "extratags": 1,
                    "zoom": zoom,
                    "accept-language": "be",
                },
                headers=request_headers,
                timeout=timeout,
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Nominatim HTTP {response.status_code}"
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Nominatim response is not an object")
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload
        except Exception as exc:
            last_error = exc
            print(
                f"  nominatim error (attempt {attempt + 1}): {exc}",
                file=sys.stderr,
            )
            if attempt + 1 < attempts:
                sleep_fn(delay)
                delay *= 2
    raise RuntimeError("Nominatim reverse failed after retries") from last_error


def geocode_collection(
    collection: dict,
    *,
    base_url: str | None = None,
    min_interval: float = MIN_INTERVAL_SECONDS,
    force: bool = False,
    write_fn: Callable[[dict], None] | None = None,
    get_fn: Callable[..., requests.Response] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    log_fn: Callable[[str], None] | None = None,
) -> list[str]:
    """Fill missing city fields. Returns OSM ids that could not be geocoded."""
    log = log_fn or print
    features = collection.get("features") or []
    targets = target_feature_indices(features, force=force)
    total = len(targets)
    failed: list[str] = []
    if total == 0:
        log("geocoded 0/0  nothing to geocode")
        return failed

    for done, index in enumerate(targets, start=1):
        feature = features[index]
        osm_id = feature_id(feature) or f"feature[{index}]"
        coords = point_lat_lon(feature)
        if coords is None:
            failed.append(osm_id)
            print(f"failed {done}/{total}  {osm_id}  missing coordinates", file=sys.stderr)
            continue
        try:
            payload = reverse_lookup(
                coords[0],
                coords[1],
                zoom=18,
                base_url=base_url,
                get_fn=get_fn,
                sleep_fn=sleep_fn,
            )
            address = payload.get("address")
            extratags = payload.get("extratags")
            if needs_town_prefix_lookup(address, extratags):
                sleep_fn(min_interval)
                town_payload = reverse_lookup(
                    coords[0],
                    coords[1],
                    zoom=12,
                    base_url=base_url,
                    get_fn=get_fn,
                    sleep_fn=sleep_fn,
                )
                extratags = town_payload.get("extratags")
            city = format_city(address, extratags)
            if not city:
                raise RuntimeError("no settlement in Nominatim address")
            properties = dict(feature.get("properties") or {})
            properties["city"] = city
            feature["properties"] = properties
            log(f"geocoded {done}/{total}  {osm_id}  {city}")
            if write_fn is not None:
                write_fn(collection)
        except Exception as exc:
            failed.append(osm_id)
            print(f"failed {done}/{total}  {osm_id}  {exc}", file=sys.stderr)
        if done < total:
            sleep_fn(min_interval)
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geojson", type=Path, default=DEFAULT_GEOJSON)
    parser.add_argument(
        "--nominatim-url",
        default=None,
        help="Override Nominatim base URL (default: NOMINATIM_URL or public OSM)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-geocode city even when the property is already set",
    )
    parser.add_argument("--delay", type=float, default=MIN_INTERVAL_SECONDS)
    args = parser.parse_args()

    collection = json.loads(args.geojson.read_text(encoding="utf-8"))

    def persist(data: dict) -> None:
        write_geojson(args.geojson, data)

    failed = geocode_collection(
        collection,
        base_url=args.nominatim_url,
        min_interval=args.delay,
        force=args.force,
        write_fn=persist,
    )
    persist(collection)
    if failed:
        print("Ungeocoded features:", file=sys.stderr)
        for osm_id in failed:
            print(f"  {osm_id}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Wrote {args.geojson}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build a slim possible_lenin.geojson from OSM Lenin points.

Drops OSM features within BUFFER_METERS of any confirmed monument,
plus a hard-coded EXCLUDED_IDS list. Keeps only fields used by the map UI.
"""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

try:
    from scripts.fetch_osm_lenins import write_geojson
except ImportError:  # running the script directly, without the package on sys.path
    from fetch_osm_lenins import write_geojson

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "scripts" / "raw_data"

BUFFER_METERS = 20
OSM_FILE = RAW_DATA_DIR / "osm_lenin.geojson"
MONUMENTS_FILE = PROJECT_ROOT / "monuments.geojson"
OUTPUT_FILE = PROJECT_ROOT / "possible_lenin.geojson"

EXCLUDED_IDS = [
    "node/9656224517",
    "node/9848094120",
    "node/6419331157",
    "node/5100170772",
    "node/4596992698",
]

def get_utm_crs(gdf: gpd.GeoDataFrame) -> str:
    """Pick a UTM zone from the first geometry (Belarus default: 34N)."""
    if len(gdf) == 0:
        return "EPSG:32634"
    first = gdf.geometry.iloc[0]
    lon = first.x if hasattr(first, "x") else first.centroid.x
    utm_zone = int((lon + 180) / 6) + 1
    return f"EPSG:326{utm_zone:02d}"


def coalesce_name(row: pd.Series) -> str:
    for key in ("name", "name:be", "name:ru", "name:en"):
        value = row.get(key)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            text = str(value).strip()
            if text and text.lower() != "nan":
                return text
    return "Ленін"


def feature_id(row: pd.Series) -> str | None:
    for key in ("@id", "id"):
        value = row.get(key)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return str(value)
    return None


def existing_cities(path: Path) -> dict[str, str]:
    """Map OSM id → city from a previously written possible layer."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cities: dict[str, str] = {}
    for feature in data.get("features") or []:
        properties = feature.get("properties") or {}
        identifier = properties.get("id")
        city = properties.get("city")
        if identifier and isinstance(city, str) and city.strip():
            cities[str(identifier)] = city.strip()
    return cities


def apply_existing_cities(collection: dict, cities: dict[str, str]) -> dict:
    """Copy cached cities onto matching features; leave new ids without city."""
    for feature in collection.get("features") or []:
        properties = feature.get("properties") or {}
        identifier = properties.get("id")
        city = cities.get(str(identifier)) if identifier else None
        ordered: dict[str, str] = {}
        if properties.get("id") is not None:
            ordered["id"] = properties["id"]
        if properties.get("name") is not None:
            ordered["name"] = properties["name"]
        if city:
            ordered["city"] = city
        if properties.get("memorial") is not None:
            ordered["memorial"] = properties["memorial"]
        feature["properties"] = ordered
    return collection


def to_slim_geojson(gdf: gpd.GeoDataFrame) -> dict:
    """Compact FeatureCollection with only id / name / memorial."""
    features = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty or geom.geom_type != "Point":
            continue

        props = {"id": feature_id(row), "name": coalesce_name(row)}
        memorial = row.get("memorial")
        if memorial is not None and not (isinstance(memorial, float) and pd.isna(memorial)):
            text = str(memorial).strip()
            if text and text.lower() != "nan":
                props["memorial"] = text

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(geom.x, 7), round(geom.y, 7)],
                },
                "properties": props,
            }
        )

    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    print(f"Loading {OSM_FILE}...")
    osm_gdf = gpd.read_file(OSM_FILE)
    print(f"Loaded {len(osm_gdf)} OSM features")

    print(f"Loading {MONUMENTS_FILE}...")
    monuments_gdf = gpd.read_file(MONUMENTS_FILE)
    print(f"Loaded {len(monuments_gdf)} monuments")

    utm_crs = get_utm_crs(monuments_gdf if len(monuments_gdf) else osm_gdf)
    print(f"Using UTM CRS: {utm_crs}")

    monuments_utm = monuments_gdf.to_crs(utm_crs)
    print(f"Building {BUFFER_METERS} m buffers around monuments...")
    monuments_utm["buffer"] = monuments_utm.geometry.buffer(BUFFER_METERS)
    combined_buffer = unary_union(monuments_utm["buffer"].values)

    osm_utm = osm_gdf.to_crs(utm_crs)
    print("Filtering OSM features outside monument buffers...")
    buffer_gdf = gpd.GeoDataFrame(geometry=[combined_buffer], crs=utm_crs)
    joined = gpd.sjoin(osm_utm, buffer_gdf, how="left", predicate="intersects")
    filtered_utm = joined[joined["index_right"].isna()]
    filtered_gdf = filtered_utm.to_crs(osm_gdf.crs)

    extra_cols = [c for c in filtered_gdf.columns if c not in osm_gdf.columns]
    if extra_cols:
        filtered_gdf = filtered_gdf.drop(columns=extra_cols)

    if EXCLUDED_IDS:
        print(f"Applying EXCLUDED_IDS ({len(EXCLUDED_IDS)})...")
        exclude_mask = pd.Series(False, index=filtered_gdf.index)

        for obj_id in EXCLUDED_IDS:
            if obj_id in filtered_gdf.index:
                exclude_mask |= filtered_gdf.index == obj_id

        if "id" in filtered_gdf.columns:
            exclude_mask |= filtered_gdf["id"].isin(EXCLUDED_IDS)
        if "@id" in filtered_gdf.columns:
            exclude_mask |= filtered_gdf["@id"].isin(EXCLUDED_IDS)

        excluded_count = int(exclude_mask.sum())
        filtered_gdf = filtered_gdf[~exclude_mask]
        if excluded_count:
            print(f"  Removed {excluded_count} excluded features")

    slim = apply_existing_cities(to_slim_geojson(filtered_gdf), existing_cities(OUTPUT_FILE))
    print(f"Writing slim {OUTPUT_FILE} ({len(slim['features'])} features)...")
    write_geojson(OUTPUT_FILE, slim)

    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print("\nDone.")
    print(f"  OSM input: {len(osm_gdf)}")
    print(f"  Monuments: {len(monuments_gdf)}")
    print(f"  Remaining possible: {len(slim['features'])}")
    print(f"  Removed near monuments: {len(osm_gdf) - len(filtered_gdf)}")
    print(f"  Output size: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()

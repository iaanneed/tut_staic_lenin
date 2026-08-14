#!/usr/bin/env python3
"""
Merge lenin3dpraty into possible_lenin.

Confirmed monuments stay in monuments.geojson.
possible_lenin is enriched / extended from the 3rd-party source,
but 3dparty points near existing monuments are not added as new possibles.

Modes (--mode):
  add_to_possible  [default]  possible + unmatched 3dparty points (not near monuments)
  report                      distance-match stats only
  enrich                      copy 3dparty fields onto matching possible points
  remove                      drop possible points that match 3dparty
  merged                      single GeoJSON with a source field on every point
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "scripts" / "raw_data"

POSSIBLE_FILE = PROJECT_ROOT / "possible_lenin.geojson"
MONUMENTS_FILE = PROJECT_ROOT / "monuments.geojson"
PARTY3D_FILE = RAW_DATA_DIR / "lenin3dpraty.geojson"
OUTPUT_POSSIBLE = PROJECT_ROOT / "possible_lenin.geojson"
OUTPUT_MERGED = PROJECT_ROOT / "merged_lenin.geojson"

# 3dparty often geocodes to city center, so use a wide match radius
BUFFER_MONUMENTS = 1000  # m around monuments: treat 3dparty inside as already confirmed
BUFFER_MATCH = 1000  # m around possible: enrich vs add as new

# Cyrillic keys are field names in lenin3dpraty.geojson
PARTY_FIELDS_MAP = {
    "Заголовок": "title_3dparty",
    "Путь": "photo_3dparty",
    "Регион": "region_3dparty",
    "Район": "district_3dparty",
    "Статус": "status_3dparty",
    "Страна": "country_3dparty",
}

EXCLUDED_STATUSES = {"демонтирован", "перенесен"}


def normalized_status(value: object) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def load_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)
    return gdf


def get_utm_crs(gdf: gpd.GeoDataFrame) -> str:
    if len(gdf) == 0:
        return "EPSG:32634"
    first = gdf.geometry.iloc[0]
    if hasattr(first, "x"):
        lon = first.x
    else:
        lon = first.centroid.x if first is not None else 27.5
    utm_zone = int((lon + 180) / 6) + 1
    return f"EPSG:326{utm_zone:02d}"


def match_by_distance(
    possible: gpd.GeoDataFrame,
    party3d: gpd.GeoDataFrame,
    buffer_m: float,
):
    """Return (possible_utm, party_utm, joined, first_match)."""
    utm_crs = get_utm_crs(possible)
    possible_utm = possible.to_crs(utm_crs)
    party_utm = party3d.to_crs(utm_crs)
    possible_utm["_buffer"] = possible_utm.geometry.buffer(buffer_m)
    possible_buffers = possible_utm.set_geometry("_buffer")
    joined = gpd.sjoin(possible_buffers, party_utm, how="left", predicate="intersects")
    first_match = joined[~joined.index_right.isna()].groupby(level=0).first()
    return possible_utm, party_utm, joined, first_match


def run_add_to_possible(
    possible: gpd.GeoDataFrame,
    party3d: gpd.GeoDataFrame,
    monuments: gpd.GeoDataFrame,
    buffer_monuments: float,
    buffer_match: float,
    output_path: Path,
) -> None:
    """
    Rewrite possible = current possible + 3dparty points not near monuments.
    Matching possible points are enriched; remaining 3dparty points are appended.
    """
    utm_crs = get_utm_crs(possible)
    possible_utm = possible.to_crs(utm_crs)
    party_utm = party3d.to_crs(utm_crs)
    mon_utm = monuments.to_crs(utm_crs).copy()

    mon_utm["_buf"] = mon_utm.geometry.buffer(buffer_monuments)
    monuments_union = unary_union(mon_utm["_buf"].values)
    buffer_gdf = gpd.GeoDataFrame(geometry=[monuments_union], crs=utm_crs)

    party_joined_mon = gpd.sjoin(party_utm, buffer_gdf, how="left", predicate="intersects")
    mon_right_col = (
        "index_right"
        if "index_right" in party_joined_mon.columns
        else next((c for c in party_joined_mon.columns if c not in party_utm.columns), None)
    )
    if mon_right_col is None:
        party_not_in_monuments_idx = party_utm.index
    else:
        party_not_in_monuments_idx = party_joined_mon[
            party_joined_mon[mon_right_col].isna()
        ].index.unique()
    party_candidates = party_utm.loc[party_not_in_monuments_idx].copy()

    # Skip entries that are no longer at their source coordinates.
    if "Статус" in party_candidates.columns:
        mask = ~party_candidates["Статус"].map(normalized_status).isin(
            EXCLUDED_STATUSES
        )
        party_candidates = party_candidates.loc[mask]

    possible_utm = possible_utm.copy()
    possible_utm["_buf"] = possible_utm.geometry.buffer(buffer_match)
    possible_buffers = possible_utm.set_geometry("_buf")
    joined = gpd.sjoin(possible_buffers, party_candidates, how="left", predicate="intersects")

    right_idx_col = "index_right" if "index_right" in joined.columns else "index_right_r"
    if right_idx_col not in joined.columns:
        cands = [c for c in joined.columns if "index" in c and c not in possible_buffers.columns]
        right_idx_col = cands[0] if cands else None
    if right_idx_col is None or right_idx_col not in joined.columns:
        raise ValueError("Could not find right-table index column after sjoin")

    possible_out = possible.copy()
    for _, key in PARTY_FIELDS_MAP.items():
        if key not in possible_out.columns:
            possible_out[key] = None
    if "source" not in possible_out.columns:
        possible_out["source"] = "osm"

    for idx in possible_out.index:
        rows = joined.loc[[idx]] if idx in joined.index else joined[joined.index == idx]
        if rows.empty or right_idx_col not in rows.columns or pd.isna(rows[right_idx_col].iloc[0]):
            continue
        party_idx = rows[right_idx_col].iloc[0]
        party_row = party3d.loc[party_idx]
        for src_key, dest_key in PARTY_FIELDS_MAP.items():
            if src_key in party_row and party_row[src_key] is not None:
                possible_out.at[idx, dest_key] = party_row[src_key]
        possible_out.at[idx, "source"] = "osm,3dparty"

    matched_party_idx = joined[right_idx_col].dropna().unique()
    party_only_idx = party_candidates.index.difference(matched_party_idx)

    new_rows = []
    for idx in party_only_idx:
        row = party3d.loc[idx]
        geom = row.geometry
        if geom is None:
            continue
        props = {
            "name": row.get("Заголовок") or "Lenin",
            "source": "3dparty",
        }
        for src_key, dest_key in PARTY_FIELDS_MAP.items():
            if src_key in row and row[src_key] is not None:
                props[dest_key] = row[src_key]
        new_rows.append({"geometry": geom, **props})

    if new_rows:
        new_gdf = gpd.GeoDataFrame(new_rows, crs=possible.crs)
        for col in possible_out.columns:
            if col not in new_gdf.columns and col != "geometry":
                new_gdf[col] = None
        for col in new_gdf.columns:
            if col not in possible_out.columns:
                possible_out[col] = None
        possible_out = pd.concat([possible_out, new_gdf], ignore_index=True)

    n_before_inactive = len(possible_out)
    if "status_3dparty" in possible_out.columns:
        inactive = possible_out["status_3dparty"].map(normalized_status).isin(
            EXCLUDED_STATUSES
        )
        possible_out = possible_out.loc[~inactive]
    n_inactive_removed = n_before_inactive - len(possible_out)

    possible_out.to_file(output_path, driver="GeoJSON")
    n_enriched = (possible_out["source"] == "osm,3dparty").sum()
    n_new = (possible_out["source"] == "3dparty").sum()
    print(f"Saved: {output_path}")
    print(
        f"  Buffers: monuments={buffer_monuments:.0f} m, "
        f"match-to-possible={buffer_match:.0f} m"
    )
    print(f"  Total points: {len(possible_out)} (was possible: {len(possible)})")
    print(f"  Enriched from 3dparty: {int(n_enriched)}, added new: {int(n_new)}")
    print(
        f"  Excluded from 3dparty (near monuments): "
        f"{len(party3d) - len(party_utm.loc[party_not_in_monuments_idx])}"
    )
    if n_inactive_removed > 0:
        print(f"  Removed dismantled or moved: {n_inactive_removed}")


def run_report(possible: gpd.GeoDataFrame, party3d: gpd.GeoDataFrame, buffer_m: float) -> int:
    _possible_utm, party_utm, joined, _first_match = match_by_distance(
        possible, party3d, buffer_m
    )
    matched = joined[~joined.index_right.isna()]
    unique_matched = matched.index.unique()
    only_party = set(party_utm.index) - set(matched["index_right"].dropna().unique())
    print(f"Possible Lenin: {len(possible)} points")
    print(f"Lenin 3D Party: {len(party3d)} points")
    print(f"Match buffer: {buffer_m} m")
    print(f"Possible points with a 3dparty match: {len(unique_matched)}")
    print(f"3dparty points with no possible match: {len(only_party)}")
    if len(unique_matched) > 0:
        print("\nSample matches (first 5):")
        for idx in list(unique_matched)[:5]:
            row = possible.loc[idx]
            name = row.get("name") or row.get("name:ru") or row.get("name:be") or "—"
            print(f"  {idx}: {name}")
    return len(unique_matched)


def run_enrich(
    possible: gpd.GeoDataFrame,
    party3d: gpd.GeoDataFrame,
    buffer_m: float,
    output_path: Path,
) -> None:
    _possible_utm, _party_utm, joined, _first_match = match_by_distance(
        possible, party3d, buffer_m
    )
    possible = possible.copy()
    for src_key, dest_key in PARTY_FIELDS_MAP.items():
        if src_key not in party3d.columns:
            continue
        possible[dest_key] = None
    for idx in possible.index:
        rows = joined.loc[[idx]] if idx in joined.index else joined[joined.index == idx]
        if rows.empty or pd.isna(rows["index_right"].iloc[0]):
            continue
        party_idx = rows["index_right"].iloc[0]
        party_row = party3d.loc[party_idx]
        for src_key, dest_key in PARTY_FIELDS_MAP.items():
            if src_key in party_row and party_row[src_key] is not None:
                possible.at[idx, dest_key] = party_row[src_key]
    possible["confirmed_3dparty"] = possible.index.isin(
        joined[~joined.index_right.isna()].index.unique()
    )
    possible.to_file(output_path, driver="GeoJSON")
    print(f"Enriched possible_lenin written: {output_path}")
    print(f"Confirmed by 3dparty: {int(possible['confirmed_3dparty'].sum())}")


def run_remove(
    possible: gpd.GeoDataFrame,
    party3d: gpd.GeoDataFrame,
    buffer_m: float,
    output_path: Path,
) -> None:
    _possible_utm, _party_utm, joined, _first_match = match_by_distance(
        possible, party3d, buffer_m
    )
    to_keep = joined[joined.index_right.isna()].index.unique()
    out = possible.loc[to_keep].copy()
    out.to_file(output_path, driver="GeoJSON")
    removed = len(possible) - len(out)
    print(f"Removed from possible (matched 3dparty): {removed}. Kept: {len(out)} -> {output_path}")


def run_merged(
    possible: gpd.GeoDataFrame,
    party3d: gpd.GeoDataFrame,
    buffer_m: float,
    output_path: Path,
) -> None:
    """One GeoJSON; each point has source in ('osm', '3dparty', 'osm,3dparty')."""
    _possible_utm, _party_utm, joined, _first_match = match_by_distance(
        possible, party3d, buffer_m
    )
    matched_possible_idx = joined[~joined.index_right.isna()].index.unique()
    matched_party_idx = joined.loc[matched_possible_idx, "index_right"].dropna().unique()

    features = []
    for idx in possible.index:
        if idx not in matched_possible_idx:
            feat = possible.loc[[idx]].__geo_interface__["features"][0]
            feat["properties"] = dict(feat.get("properties", {}))
            feat["properties"]["source"] = "osm"
            features.append(feat)

    for idx in matched_possible_idx:
        feat = possible.loc[[idx]].__geo_interface__["features"][0]
        props = dict(feat.get("properties", {}))
        props["source"] = "osm,3dparty"
        party_idx = joined.loc[idx, "index_right"]
        if isinstance(party_idx, pd.Series):
            party_idx = party_idx.iloc[0]
        elif hasattr(party_idx, "__len__") and not isinstance(party_idx, str):
            party_idx = party_idx[0]
        party_row = party3d.loc[party_idx]
        for src_key, dest_key in PARTY_FIELDS_MAP.items():
            if src_key in party_row and party_row[src_key] is not None:
                props[dest_key] = party_row[src_key]
        feat["properties"] = props
        features.append(feat)

    for idx in party3d.index:
        if idx not in matched_party_idx:
            feat = party3d.loc[[idx]].__geo_interface__["features"][0]
            props = dict(feat.get("properties", {}))
            props["source"] = "3dparty"
            feat["properties"] = props
            features.append(feat)

    with open(output_path, "w", encoding="utf-8") as out:
        json.dump({"type": "FeatureCollection", "features": features}, out, ensure_ascii=False, indent=2)
    print(f"Merged layer: {output_path}, points: {len(features)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge lenin3dpraty into possible_lenin")
    parser.add_argument(
        "--mode",
        choices=["add_to_possible", "report", "enrich", "remove", "merged"],
        default="add_to_possible",
        help="Pipeline mode (default: add_to_possible)",
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=None,
        help="Match buffer in meters for report/enrich/remove/merged (default: 1000)",
    )
    parser.add_argument(
        "--buffer-monuments",
        type=float,
        default=BUFFER_MONUMENTS,
        help=f"Monument buffer in m (default: {BUFFER_MONUMENTS})",
    )
    parser.add_argument(
        "--buffer-match",
        type=float,
        default=BUFFER_MATCH,
        help=f"Possible-match buffer in m (default: {BUFFER_MATCH})",
    )
    parser.add_argument("--possible", type=Path, default=POSSIBLE_FILE)
    parser.add_argument("--monuments", type=Path, default=MONUMENTS_FILE)
    parser.add_argument("--party3d", type=Path, default=PARTY3D_FILE)
    args = parser.parse_args()

    if not args.possible.exists():
        print(f"File not found: {args.possible}", file=sys.stderr)
        sys.exit(1)
    if not args.party3d.exists():
        print(f"File not found: {args.party3d}", file=sys.stderr)
        sys.exit(1)

    possible = load_geojson(args.possible)
    party3d = load_geojson(args.party3d)
    buffer = args.buffer if args.buffer is not None else BUFFER_MATCH

    if args.mode == "add_to_possible":
        if not args.monuments.exists():
            print(f"File not found: {args.monuments}", file=sys.stderr)
            sys.exit(1)
        monuments = load_geojson(args.monuments)
        run_add_to_possible(
            possible,
            party3d,
            monuments,
            args.buffer_monuments,
            args.buffer_match,
            OUTPUT_POSSIBLE,
        )
    elif args.mode == "report":
        run_report(possible, party3d, buffer)
    elif args.mode == "enrich":
        run_enrich(possible, party3d, buffer, OUTPUT_POSSIBLE)
    elif args.mode == "remove":
        run_remove(possible, party3d, buffer, OUTPUT_POSSIBLE)
    elif args.mode == "merged":
        run_merged(possible, party3d, buffer, OUTPUT_MERGED)


if __name__ == "__main__":
    main()

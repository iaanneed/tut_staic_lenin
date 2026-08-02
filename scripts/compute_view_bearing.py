"""
Computes a suggested camera `viewBearing` for each monument in monuments.geojson.

Heuristic:
  1. Find the nearest road (OSM `highway=*`) within ROAD_RADIUS_M of the monument.
     Prefer a "real" road over footway/path/service/etc; only fall back to those
     if nothing else is nearby.
  2. The direction from the monument to the *closest point* on that road is the
     direction the monument's face looks (most Lenin statues face the nearest
     road). That's unambiguous -- no need to guess between two perpendicular
     candidates.
  3. viewBearing = that azimuth + 180 (camera stands on the road side, looking
     back at the monument).
  4. If no usable road is found within MAX_USABLE_ROAD_DIST, viewBearing is left
     unset -- an earlier version fell back to a building-openness heuristic, but
     it agreed with the road heuristic no better than chance, so it was dropped.

By default only monuments without a `viewBearing` property are processed. Use
`--force` to intentionally recompute every monument.

Talks to the public Overpass API (OpenStreetMap) -- respects it with batched
requests and a short delay between them. Raw responses are cached in
scripts/raw_data/overpass_road_cache_v2.json so re-runs (e.g. after tuning thresholds)
don't re-hit the network.
"""
import argparse
import json
import math
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
MONUMENTS_PATH = os.path.join(PROJECT_ROOT, "monuments.geojson")
CACHE_PATH = os.path.join(HERE, "raw_data", "overpass_road_cache_v2.json")

ROAD_RADIUS_M = 50
ATTRIBUTION_MAX_DIST = 150
MAX_USABLE_ROAD_DIST = 35
BATCH_SIZE = 15

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "tut-staic-lenin-view-bearing/1.0 (contact: ivan.liadzian@mapbox.com)"}

MINOR_HIGHWAY_TYPES = {"footway", "path", "steps", "pedestrian", "cycleway", "track", "service"}


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def local_xy(lat0, lon0, lat, lon):
    """Small-scale equirectangular approx (fine at <100m): x=east meters, y=north meters."""
    mx = 111320.0 * math.cos(math.radians(lat0))
    my = 110540.0
    return (lon - lon0) * mx, (lat - lat0) * my


def closest_point_on_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    seg_len2 = dx * dx + dy * dy
    t = 0.0 if seg_len2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len2))
    cx, cy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - cx, py - cy), cx, cy


def overpass_query(clauses):
    query = f"[out:json][timeout:90];\n(\n{clauses}\n);\nout geom;\n"
    last_error = None
    for attempt in range(3):
        try:
            r = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=120)
            r.raise_for_status()
            return r.json().get("elements", [])
        except Exception as e:
            last_error = e
            print(f"  overpass error (attempt {attempt + 1}): {e}", file=sys.stderr)
            time.sleep(5)
    raise RuntimeError("Overpass API failed after 3 attempts") from last_error


def point_cache_key(point):
    return f'{point["lat"]:.7f},{point["lon"]:.7f}'


def fetch_roads(points, cache):
    """Return nearby roads per point, caching each coordinate independently."""
    roads_cache = cache.setdefault("roads_by_point", {})
    roads_by_point = {}
    missing = []

    for index, point in enumerate(points):
        key = point_cache_key(point)
        if key in roads_cache:
            roads_by_point[index] = roads_cache[key]
        else:
            missing.append((index, point))

    if roads_by_point:
        print(f"  using cached road data for {len(roads_by_point)} points", file=sys.stderr)

    for start in range(0, len(missing), BATCH_SIZE):
        indexed_batch = missing[start:start + BATCH_SIZE]
        batch = [point for _, point in indexed_batch]
        print(f"  fetching road batch with {len(batch)} points ...", file=sys.stderr)
        clauses = "\n".join(
            f'  way["highway"](around:{ROAD_RADIUS_M},{p["lat"]:.7f},{p["lon"]:.7f});' for p in batch
        )
        elements = overpass_query(clauses)
        assigned = attribute_to_nearest_point(elements, batch)
        for local_index, (global_index, point) in enumerate(indexed_batch):
            roads = assigned[local_index]
            roads_by_point[global_index] = roads
            roads_cache[point_cache_key(point)] = roads
        time.sleep(2)

    cache.pop("highway", None)
    return roads_by_point


def attribute_to_nearest_point(elements, points):
    """Assign each road way to whichever query point's nearest node is closest."""
    assigned = {i: [] for i in range(len(points))}
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        hw = el.get("tags", {}).get("highway")
        if hw is None:
            continue
        coords = [(node["lon"], node["lat"]) for node in el["geometry"]]
        if len(coords) < 2:
            continue

        best_i, best_d = None, None
        for i, p in enumerate(points):
            for lon, lat in coords:
                d = haversine_m(p["lat"], p["lon"], lat, lon)
                if best_d is None or d < best_d:
                    best_d, best_i = d, i
        if best_d is not None and best_d <= ATTRIBUTION_MAX_DIST:
            assigned[best_i].append((hw, coords))
    return assigned


def road_bearing_for_point(p, ways):
    """Returns (dist, azimuth_to_road, highway_type) or None."""
    best = None
    best_excluding_minor = None
    for hw, coords in ways:
        pts_local = [local_xy(p["lat"], p["lon"], lat, lon) for lon, lat in coords]
        for a, b in zip(pts_local, pts_local[1:]):
            d, cx, cy = closest_point_on_segment(0.0, 0.0, a[0], a[1], b[0], b[1])
            azimuth = math.degrees(math.atan2(cx, cy)) % 360
            if best is None or d < best[0]:
                best = (d, azimuth, hw)
            if hw not in MINOR_HIGHWAY_TYPES and (best_excluding_minor is None or d < best_excluding_minor[0]):
                best_excluding_minor = (d, azimuth, hw)
    return best_excluding_minor or best


def target_feature_indices(features, force=False):
    if force:
        return list(range(len(features)))
    return [
        index
        for index, feature in enumerate(features)
        if "viewBearing" not in feature.get("properties", {})
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute viewBearing for every monument",
    )
    args = parser.parse_args()

    data = json.load(open(MONUMENTS_PATH, encoding="utf-8"))
    features = data["features"]
    target_indices = target_feature_indices(features, force=args.force)
    if not target_indices:
        print("No monuments need a viewBearing calculation", file=sys.stderr)
        return

    points = [
        {
            "lat": features[index]["geometry"]["coordinates"][1],
            "lon": features[index]["geometry"]["coordinates"][0],
        }
        for index in target_indices
    ]
    print(
        f"Loaded {len(features)} monuments; processing {len(points)}",
        file=sys.stderr,
    )

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    cache = json.load(open(CACHE_PATH, encoding="utf-8")) if os.path.exists(CACHE_PATH) else {}

    roads_by_point = fetch_roads(points, cache)
    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"))

    n_road, n_none = 0, 0
    for i, (feature_index, p) in enumerate(zip(target_indices, points)):
        feature = features[feature_index]
        road = road_bearing_for_point(p, roads_by_point[i])
        if road is not None and road[0] <= MAX_USABLE_ROAD_DIST:
            _, azimuth, _ = road
            feature["properties"]["viewBearing"] = round((azimuth + 180) % 360, 1)
            n_road += 1
        else:
            feature["properties"]["viewBearing"] = None
            n_none += 1
        feature["properties"].pop("viewBearingSource", None)
        feature["properties"].pop("viewBearingRoad", None)
        feature["properties"].pop("viewBearingRoadDist", None)
        feature["properties"].pop("viewBearingRoadType", None)

    json.dump(data, open(MONUMENTS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Done: {n_road} road-based, {n_none} no nearby road -> {MONUMENTS_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()

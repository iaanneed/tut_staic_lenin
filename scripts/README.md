# Data scripts

Utilities for importing, validating, and maintaining the map data.

## Scripts

- `fetch_telegram_channel.py` — downloads new photo posts from the public
  Telegram channel and creates a temporary Telegram Desktop-compatible export.
- `parse_new_monuments.py` — parses a Telegram export, copies photos, and appends
  new confirmed monuments to `monuments.geojson`.
- `compute_view_bearing.py` — uses nearby OpenStreetMap roads from Overpass to
  calculate camera bearings. By default, it only processes monuments without a
  `viewBearing` property.
- `fetch_osm_lenins.py` — queries Overpass for Lenin memorials in Belarus
  (nodes, ways, and relations) and writes point features to
  `scripts/raw_data/osm_lenin.geojson`. Its `write_geojson` helper sorts and
  indents generated collections and is reused for the possible layer.
- `compare_geojson.py` — rebuilds `possible_lenin.geojson` from the raw OSM
  dataset, excluding points near confirmed monuments.
- `validate_data.py` — validates GeoJSON structure, unique Telegram source IDs,
  coordinates, and referenced photo files.
- `build_sync_pr_body.py` — describes the monuments, possible points, and
  bearings changed by an automated synchronization pull request.
- `merge_lenin_sources.py` — enriches or merges possible monuments with the
  third-party Lenin dataset (manual only; not part of the sync workflow).

Raw source files and API caches live under `scripts/raw_data/` and are not
committed.

## Telegram synchronization

The `Sync Telegram monuments` workflow is started manually from the repository's
Actions tab. It:

1. Fetches Telegram posts newer than the largest committed `source_id`
2. Appends new monuments and computes missing `viewBearing` values
3. Fetches current OSM Lenins via Overpass and rebuilds `possible_lenin.geojson`
4. Validates the result and opens a pull request when data changed

The pull request description lists the added monuments, the OSM candidates added
to and removed from the possible layer, and the recomputed bearings. Overpass
returns features in an unstable order, so generated files are sorted before they
are written; without that, every rebuild would rewrite the whole file.


Configure these repository Actions secrets:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION`

The API credentials come from [my.telegram.org](https://my.telegram.org/).
Generate `TELEGRAM_SESSION` once on a trusted computer:

```python
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print(client.session.save())
```

The session string represents an authorized Telegram device. Never commit or
print it; a dedicated read-only account is recommended.

Repository Actions settings must allow workflows to write contents and create
pull requests.

## Local checks

```sh
uv sync --group dev
uv run pytest
uv run python scripts/validate_data.py
```

## Overpass query for OSM Lenins

API-compatible query used by `fetch_osm_lenins.py` (no Overpass Turbo macros):

```
[out:json][timeout:90];
area["ISO3166-1"="BY"][admin_level=2]->.searchArea;
(
  nwr["historic"~"^(memorial|monument)$"]["name"~"Ленін|Ленин|Lenin",i](area.searchArea);
  nwr["historic"~"^(memorial|monument)$"]["name:ru"~"Ленин",i](area.searchArea);
  nwr["historic"~"^(memorial|monument)$"]["name:be"~"Ленін",i](area.searchArea);
);
out center;
```

Ways and relations are stored as their Overpass `center` point.

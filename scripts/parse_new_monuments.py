#!/usr/bin/env python3
"""Parse a Telegram chat export and append new Lenin monuments to monuments.geojson."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "scripts" / "raw_data"
MONUMENTS_GEOJSON = PROJECT_ROOT / "monuments.geojson"
TARGET_PHOTOS_DIR = PROJECT_ROOT / "photos"

# Message IDs to skip (outside Belarus / wrong content)
EXCLUDED_IDS = [
    36,  # Масква
    40,  # Смаленск
    33,  # Леніна скралі, замест яго нейкія дурыны
]

# Region hashtag variants (lowercase) -> canonical tag
REGION_HASHTAGS = {
    "брэсцкая": "#Брэсцкая",
    "брэстская": "#Брэсцкая",
    "віцебская": "#Віцебская",
    "витебская": "#Віцебская",
    "гомельская": "#Гомельская",
    "гродзенская": "#Гродзенская",
    "гродненская": "#Гродзенская",
    "магілеўская": "#Магілеўская",
    "магілёўская": "#Магілеўская",
    "могилевская": "#Магілеўская",
    "мінская": "#Мінская",
    "минская": "#Мінская",
}

MONUMENT_TYPE_HASHTAGS = {
    "бюст": "#Бюст",
}


def find_latest_chatexport(raw_data_dir: Path) -> Path | None:
    """Return the newest ChatExport_* directory under raw_data."""
    exports = sorted(
        (p for p in raw_data_dir.glob("ChatExport_*") if p.is_dir()),
        key=lambda p: p.name,
    )
    return exports[-1] if exports else None


def extract_text_from_entities(text_entities) -> str:
    """Flatten Telegram text / text_entities into a plain string."""
    if not text_entities:
        return ""

    parts: list[str] = []
    for entity in text_entities:
        if isinstance(entity, dict):
            entity_type = entity.get("type")
            text = entity.get("text", "")
            if entity_type == "hashtag":
                if not text.startswith("#"):
                    text = "#" + text
                parts.append(text)
            elif entity_type in {"plain", "italic", "code"}:
                parts.append(text)
        elif isinstance(entity, str):
            parts.append(entity)

    return "".join(parts)


def extract_city_from_text(text: str) -> str | None:
    """Extract settlement name. Name must start with a letter (avoids '(1936 г.)')."""
    # More specific prefixes first
    patterns = [
        (r"г\.п\.\s*([А-Яа-яЁёІіЎў][^\n,]*)", "г.п. {}"),
        (r"аг\.\s*([А-Яа-яЁёІіЎў][^\n,]*)", "аг. {}"),
        (r"п\.\s*([А-Яа-яЁёІіЎў][^\n,]*)", "п. {}"),
        (r"Ст\.\s*([А-Яа-яЁёІіЎў][^\n,]*)", "Ст. {}"),
        (r"г\.\s*([А-Яа-яЁёІіЎў][^\n,]*)", "г. {}"),
    ]

    for pattern, template in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            city_name = match.group(1).strip()
            city_name = re.sub(r"\s+", " ", city_name)
            city_name = re.sub(r"📍.*$", "", city_name).strip()
            return template.format(city_name)

    return None


def extract_hashtags_from_text(text: str) -> tuple[str | None, str | None]:
    """Return (regionHashtag, monumentType) from all hashtags in the text."""
    region = None
    monument_type = None

    for tag_text in re.findall(r"#+([А-Яа-яЁёІіЎў]+)", text):
        tag_lower = tag_text.lower()
        if tag_lower in REGION_HASHTAGS and region is None:
            region = REGION_HASHTAGS[tag_lower]
        elif tag_lower in MONUMENT_TYPE_HASHTAGS and monument_type is None:
            monument_type = MONUMENT_TYPE_HASHTAGS[tag_lower]

    return region, monument_type


def extract_title_from_text(text: str) -> str | None:
    """Take description lines before city / hashtag / coordinates."""
    title_parts: list[str] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^г\.|^г\.п\.|^аг\.|^п\.|^Ст\.|^#|^📍", line, re.IGNORECASE):
            break
        if re.match(r"^\d+\.\d+,\s*\d+\.\d+", line):
            continue
        title_parts.append(line)

    title = " ".join(title_parts).strip()
    return title or None


def extract_coordinates_from_text(text: str) -> list[float] | None:
    """Extract [lon, lat] if coordinates look like Belarus/region."""
    patterns = [
        r"📍\s*(\d+\.\d+)\s*,\s*(\d+\.\d+)",
        r"(\d+\.\d+)\s*,\s*(\d+\.\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
            if 50 <= lat <= 60 and 20 <= lon <= 35:
                return [lon, lat]

    return None


def parse_message(message: dict) -> dict | None:
    """Parse one Telegram message into monument fields, or None if not a photo post."""
    if message.get("type") != "message" or not message.get("photo"):
        return None

    text_entities = message.get("text_entities", [])
    text = extract_text_from_entities(text_entities)

    if not text and message.get("text"):
        if isinstance(message["text"], list):
            text = extract_text_from_entities(message["text"])
        else:
            text = message["text"]

    city = extract_city_from_text(text)
    region_hashtag, monument_type = extract_hashtags_from_text(text)
    title = extract_title_from_text(text)
    coordinates = extract_coordinates_from_text(text) or [0, 0]

    photo_path = message.get("photo", "")
    return {
        "source_id": message.get("id"),
        "source_date": message.get("date"),
        "city": city,
        "title": title,
        "regionHashtag": region_hashtag,
        "monumentType": monument_type,
        "photo_filename": os.path.basename(photo_path),
        "coordinates": coordinates,
        "photo_path": photo_path,
    }


def load_existing_monuments(path: Path = MONUMENTS_GEOJSON) -> dict:
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def create_feature(monument_data: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": monument_data["coordinates"],
        },
        "properties": {
            "city": monument_data["city"],
            "title": monument_data["title"],
            "regionHashtag": monument_data["regionHashtag"],
            "monumentType": monument_data.get("monumentType"),
            "imageUrl_preview": f"photos/{monument_data['photo_filename']}",
            "imageUrl_full": f"photos/{monument_data['photo_filename']}",
            "source_id": monument_data["source_id"],
            "source_date": monument_data["source_date"],
        },
    }


def copy_photo(
    source_photos_dir: Path,
    source_filename: str,
    target_filename: str,
    target_photos_dir: Path = TARGET_PHOTOS_DIR,
) -> bool:
    source_path = source_photos_dir / source_filename
    target_path = target_photos_dir / target_filename

    if not source_path.exists():
        print(f"Photo not found: {source_path}")
        return False

    target_photos_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, target_path)
        print(f"Copied photo: {target_filename}")
        return True
    except OSError as e:
        print(f"Failed to copy {source_filename}: {e}")
        return False


def ingest_export(
    export_dir: Path,
    *,
    dry_run: bool = False,
    require_coordinates: bool = False,
    monuments_path: Path = MONUMENTS_GEOJSON,
    target_photos_dir: Path = TARGET_PHOTOS_DIR,
) -> list[int]:
    """Append new monuments from a ChatExport-compatible directory.

    Returns the source IDs that were actually added. Existing features are never
    updated, which keeps repeated runs idempotent.
    """
    result_json = export_dir / "result.json"
    source_photos_dir = export_dir / "photos"

    print(f"Using export: {export_dir}")
    if not result_json.exists():
        raise FileNotFoundError(f"File not found: {result_json}")

    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    print(f"Messages: {len(messages)}")

    monuments = load_existing_monuments(monuments_path)
    existing_ids = {
        f.get("properties", {}).get("source_id") for f in monuments.get("features", [])
    }
    print(f"Existing monuments: {len(existing_ids)}")

    new_monuments: list[dict] = []
    skipped = {
        "existing": 0,
        "excluded": 0,
        "no_city": 0,
        "no_coordinates": 0,
        "no_photo": 0,
        "wrong_type": 0,
    }

    for message in messages:
        if message.get("type") != "message":
            skipped["wrong_type"] += 1
            continue

        if not message.get("photo"):
            skipped["no_photo"] += 1
            continue

        monument_data = parse_message(message)
        if not monument_data:
            continue

        source_id = monument_data["source_id"]
        if source_id in existing_ids:
            skipped["existing"] += 1
            continue

        if source_id in EXCLUDED_IDS:
            print(f"Skipped {source_id}: excluded")
            skipped["excluded"] += 1
            continue

        if not monument_data["city"]:
            print(f"Skipped {source_id}: no city")
            skipped["no_city"] += 1
            continue

        if require_coordinates and monument_data["coordinates"] == [0, 0]:
            print(f"Skipped {source_id}: no coordinates")
            skipped["no_coordinates"] += 1
            continue

        new_monuments.append(monument_data)

    zero_coords = sum(1 for m in new_monuments if m.get("coordinates") == [0, 0])

    print("\nStats:")
    print(f"  skipped (already present): {skipped['existing']}")
    print(f"  skipped (excluded): {skipped['excluded']}")
    print(f"  skipped (no city): {skipped['no_city']}")
    print(f"  skipped (no coordinates): {skipped['no_coordinates']}")
    print(f"  skipped (no photo): {skipped['no_photo']}")
    print(f"  skipped (wrong type): {skipped['wrong_type']}")
    print(f"  new monuments: {len(new_monuments)}")
    print(f"  with zero coordinates: {zero_coords}")

    if not new_monuments:
        print("\nNo new monuments found.")
        return []

    if dry_run:
        print("\nDry run — nothing written.")
        for monument in new_monuments:
            print(
                f"  would add: {monument['city']} "
                f"(id={monument['source_id']}, {monument.get('regionHashtag')}, "
                f"{monument.get('monumentType')})"
            )
        return []

    print("\nAdding monuments...")
    added_ids: list[int] = []
    for monument_data in new_monuments:
        photo_filename = monument_data["photo_filename"]
        if copy_photo(
            source_photos_dir,
            photo_filename,
            photo_filename,
            target_photos_dir,
        ):
            monuments["features"].append(create_feature(monument_data))
            added_ids.append(monument_data["source_id"])
            hashtag = monument_data.get("regionHashtag") or "no region"
            mtype = monument_data.get("monumentType")
            type_info = f", {mtype}" if mtype else ""
            print(
                f"  Added: {monument_data['city']} "
                f"(id={monument_data['source_id']}, {hashtag}{type_info})"
            )
        else:
            print(f"  Skipped (photo copy failed): {monument_data['city']}")

    with open(monuments_path, "w", encoding="utf-8") as f:
        json.dump(monuments, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Added {len(added_ids)} monuments to {monuments_path}")
    return added_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse Telegram ChatExport and append new monuments."
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=None,
        help="ChatExport directory (default: newest under scripts/raw_data/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report only; do not write geojson or copy photos",
    )
    parser.add_argument(
        "--require-coordinates",
        action="store_true",
        help="Skip posts without valid coordinates (recommended for automation)",
    )
    parser.add_argument(
        "--added-ids-output",
        type=Path,
        default=None,
        help="Write added Telegram source IDs as JSON",
    )
    args = parser.parse_args()

    export_dir = args.export
    if export_dir is None:
        export_dir = find_latest_chatexport(RAW_DATA_DIR)
        if export_dir is None:
            print(f"No ChatExport_* folder found in {RAW_DATA_DIR}")
            return
    elif not export_dir.is_absolute():
        export_dir = PROJECT_ROOT / export_dir

    try:
        added_ids = ingest_export(
            export_dir,
            dry_run=args.dry_run,
            require_coordinates=args.require_coordinates,
        )
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.added_ids_output is not None:
        args.added_ids_output.parent.mkdir(parents=True, exist_ok=True)
        args.added_ids_output.write_text(
            json.dumps(added_ids),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a pull request description from Telegram synchronization changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def possible_key(feature: dict) -> str:
    properties = feature.get("properties", {})
    if properties.get("id"):
        return str(properties["id"])
    geometry = feature.get("geometry", {})
    return json.dumps(
        [properties.get("source"), geometry.get("coordinates")],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_body(
    monuments_before: dict,
    monuments_after: dict,
    possible_before: dict,
    possible_after: dict,
    added_ids: list[int],
) -> str:
    after_by_id = {
        feature.get("properties", {}).get("source_id"): feature
        for feature in monuments_after.get("features", [])
    }
    before_by_id = {
        feature.get("properties", {}).get("source_id"): feature
        for feature in monuments_before.get("features", [])
    }

    added_lines = []
    for source_id in added_ids:
        feature = after_by_id.get(source_id)
        if feature is None:
            continue
        properties = feature.get("properties", {})
        city = clean_text(properties.get("city"), "Unknown location")
        title = clean_text(properties.get("title"), "Lenin monument")
        added_lines.append(f"- Telegram #{source_id}: {city} — {title}")

    bearing_lines = []
    for source_id, feature in after_by_id.items():
        if source_id in added_ids or source_id not in before_by_id:
            continue
        before_properties = before_by_id[source_id].get("properties", {})
        after_properties = feature.get("properties", {})
        if (
            "viewBearing" in before_properties
            and before_properties.get("viewBearing") == after_properties.get("viewBearing")
        ):
            continue
        city = clean_text(after_properties.get("city"), "Unknown location")
        value = after_properties.get("viewBearing")
        bearing = "none found" if value is None else f"{value}°"
        bearing_lines.append(f"- Telegram #{source_id}: {city} — {bearing}")

    after_possible_keys = {
        possible_key(feature) for feature in possible_after.get("features", [])
    }
    removed_lines = []
    for feature in possible_before.get("features", []):
        if possible_key(feature) in after_possible_keys:
            continue
        properties = feature.get("properties", {})
        identifier = clean_text(properties.get("id"), "coordinate candidate")
        name = clean_text(
            properties.get("name") or properties.get("title_3dparty"),
            "Lenin candidate",
        )
        removed_lines.append(f"- {identifier}: {name}")

    sections = [
        "This PR synchronizes the map with the latest Telegram channel posts.",
        "",
        "## Added monuments",
        *(added_lines or ["- None."]),
        "",
        "## Removed from the possible layer",
        *(removed_lines or ["- None."]),
    ]
    if bearing_lines:
        sections.extend(["", "## Updated view bearings", *bearing_lines])
    return "\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monuments-before", type=Path, required=True)
    parser.add_argument("--monuments-after", type=Path, required=True)
    parser.add_argument("--possible-before", type=Path, required=True)
    parser.add_argument("--possible-after", type=Path, required=True)
    parser.add_argument("--added-ids", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    body = build_body(
        load_json(args.monuments_before),
        load_json(args.monuments_after),
        load_json(args.possible_before),
        load_json(args.possible_after),
        load_json(args.added_ids),
    )
    args.output.write_text(body, encoding="utf-8")


if __name__ == "__main__":
    main()

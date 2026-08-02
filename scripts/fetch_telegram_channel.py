#!/usr/bin/env python3
"""Fetch new photo posts from a public Telegram channel via MTProto."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MONUMENTS = PROJECT_ROOT / "monuments.geojson"
DEFAULT_CHANNEL = "tut_staic_lenin"


def latest_source_id(monuments_path: Path) -> int:
    """Return the largest Telegram message ID already stored in GeoJSON."""
    if not monuments_path.exists():
        return 0

    data = json.loads(monuments_path.read_text(encoding="utf-8"))
    source_ids = [
        feature.get("properties", {}).get("source_id")
        for feature in data.get("features", [])
    ]
    numeric_ids = [value for value in source_ids if isinstance(value, int)]
    return max(numeric_ids, default=0)


def telegram_export_message(message_id: int, date: str, text: str, photo: str) -> dict:
    """Build the subset of Telegram Desktop's export schema used by the parser."""
    return {
        "id": message_id,
        "type": "message",
        "date": date,
        "photo": photo,
        "text": text,
        "text_entities": [{"type": "plain", "text": text}],
    }


async def fetch_channel(
    *,
    channel: str,
    min_id: int,
    output_dir: Path,
    api_id: int,
    api_hash: str,
    session_string: str,
) -> int:
    """Download photo posts newer than min_id into a ChatExport-like directory."""
    photos_dir = output_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    messages: list[dict] = []
    async with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        entity = await client.get_entity(channel)
        async for message in client.iter_messages(entity, min_id=min_id, reverse=True):
            if not message.photo:
                continue

            date = message.date
            timestamp = date.strftime("%d-%m-%Y_%H-%M-%S")
            requested_path = photos_dir / f"photo_{message.id}@{timestamp}"
            downloaded = await message.download_media(file=str(requested_path))
            if not downloaded:
                raise RuntimeError(f"Telegram did not download photo for message {message.id}")

            downloaded_path = Path(downloaded)
            relative_photo = f"photos/{downloaded_path.name}"
            messages.append(
                telegram_export_message(
                    message.id,
                    date.isoformat(),
                    message.raw_text or "",
                    relative_photo,
                )
            )

    export = {
        "name": channel,
        "type": "public_channel",
        "messages": messages,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(messages)


def required_environment() -> tuple[int, str, str]:
    missing = [
        name
        for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer") from exc
    return api_id, os.environ["TELEGRAM_API_HASH"], os.environ["TELEGRAM_SESSION"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--monuments", type=Path, default=DEFAULT_MONUMENTS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--min-id",
        type=int,
        default=None,
        help="Override the last source ID inferred from monuments.geojson",
    )
    args = parser.parse_args()

    try:
        api_id, api_hash, session_string = required_environment()
        min_id = args.min_id
        if min_id is None:
            min_id = latest_source_id(args.monuments)
        print(f"Fetching @{args.channel.lstrip('@')} after message {min_id}")
        count = asyncio.run(
            fetch_channel(
                channel=args.channel,
                min_id=min_id,
                output_dir=args.output,
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
            )
        )
        print(f"Downloaded {count} new photo posts")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

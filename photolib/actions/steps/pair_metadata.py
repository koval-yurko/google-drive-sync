"""Extract every sidecar and match it to the media file it describes.

Matching is global rather than per-archive: measurements show 88% of sidecars
live in a different archive part from their media, so a per-archive pass would
fail for the overwhelming majority of the export.
"""

from __future__ import annotations

import json
from typing import Iterator

from photolib import archives, takeout
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo
from photolib.ziparchive.reader import CorruptEntryError, ZipEntry

ID = "pair_metadata"
TITLE = "Pair Metadata"
DESCRIPTION = (
    "Read every metadata sidecar and match it to its photo or video, including "
    "the majority whose sidecar sits in a different archive part."
)
ORDER = 20


class Params(ActionParams):
    pass


def _timestamp(payload: dict, key: str) -> int | None:
    raw = (payload.get(key) or {}).get("timestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_sidecar(payload: dict) -> dict:
    """Flatten Google's nested sidecar shape into flat catalog columns."""
    geo = payload.get("geoData") or {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    # Google writes 0.0/0.0 when a photo carries no location.
    if not lat and not lon:
        lat = lon = None

    origin = payload.get("googlePhotosOrigin") or {}
    device = (origin.get("mobileUpload") or {}).get("deviceType")

    return {
        "title": payload.get("title"),
        "photo_taken_time": _timestamp(payload, "photoTakenTime"),
        "creation_time": _timestamp(payload, "creationTime"),
        "latitude": lat,
        "longitude": lon,
        "altitude": geo.get("altitude"),
        "url": payload.get("url"),
        "device": device,
    }


def _entry_for(row) -> ZipEntry:
    return ZipEntry(
        path=row["path"], name=row["name"], crc32=row["crc32"], size=row["size"],
        compressed_size=row["compressed_size"], method=row["method"],
        local_header_offset=row["local_header_offset"],
    )


def _match(candidate: str, by_name: dict, by_prefix: list[tuple[str, object]]):
    """Exact name first, then truncated-prefix — the two Takeout naming quirks."""
    if candidate in by_name:
        return by_name[candidate]
    for name, row in by_prefix:
        if name.startswith(candidate):
            return row
    return None


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    scan_repo = ScanRepo(ctx.conn)
    media_repo = MediaRepo(ctx.conn)

    media_rows = scan_repo.entries_of_kind("media")
    sidecar_rows = scan_repo.entries_of_kind("sidecar")
    if not media_rows:
        yield ProgressEvent(
            "No indexed media. Run Scan Archives first.", progress=1.0, level="error"
        )
        return

    for row in media_rows:
        media_repo.upsert_media(row["id"])
    yield ProgressEvent(f"{len(media_rows)} media file(s) catalogued.", progress=0.05)

    by_name = {row["name"]: row for row in media_rows}
    by_prefix = sorted((row["name"], row) for row in media_rows)

    paired = orphaned = unreadable = 0
    total = max(len(sidecar_rows), 1)

    for index, row in enumerate(sidecar_rows, start=1):
        if index % 50 == 0 or index == len(sidecar_rows):
            yield ProgressEvent(
                f"Paired {paired} of {index} sidecar(s).",
                progress=0.05 + 0.9 * index / total,
            )

        try:
            payload = json.loads(
                archives.extract_from_archive(
                    ctx.drive, row["archive_drive_id"], _entry_for(row)
                )
            )
        except (CorruptEntryError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            unreadable += 1
            yield ProgressEvent(
                f"{row['name']}: unreadable sidecar — {exc}", level="warn"
            )
            continue

        parsed = parse_sidecar(payload)
        sidecar_id = media_repo.save_sidecar(
            row["id"], parsed, json.dumps(payload, ensure_ascii=False)
        )

        candidate = takeout.media_name_for_sidecar(row["name"])
        match = _match(candidate, by_name, by_prefix)
        if match is None and parsed["title"]:
            match = _match(parsed["title"], by_name, by_prefix)

        if match is None:
            orphaned += 1
            continue

        media_repo.link_sidecar(match["id"], sidecar_id)
        paired += 1

    detail = f"Paired {paired} sidecar(s) to media."
    if orphaned:
        detail += f" {orphaned} sidecar(s) matched no media."
    if unreadable:
        detail += f" {unreadable} sidecar(s) were unreadable."
    yield ProgressEvent(detail, progress=1.0, level="warn" if orphaned else "info")

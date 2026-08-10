"""Decide where every media file goes, without moving anything.

Writes nothing to Drive. Re-running replaces the previous plan, so it is safe to
run repeatedly while tuning.

Duplicate verdicts are recorded for information only. Per the operator's
decision they never withhold a file from upload — every media row stays
`pending`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterator

from photolib import places, takeout
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

ID = "plan_organize"
TITLE = "Plan Organization"
DESCRIPTION = (
    "Resolve a capture date, place, duplicate verdict and destination for every "
    "media file. Writes nothing to Drive and can be re-run at will."
)
ORDER = 30


class Params(ActionParams):
    pass


def resolve_capture(row, sidecar, archive_modified: str | None) -> tuple[int | None, str]:
    """Best available capture time, and which source supplied it."""
    if sidecar:
        if sidecar["photo_taken_time"]:
            return sidecar["photo_taken_time"], "photo_taken_time"
        if sidecar["creation_time"]:
            return sidecar["creation_time"], "creation_time"

    year = takeout.year_from_path(row["path"])
    if year is not None:
        stamp = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        return int(stamp), "year_folder"

    if archive_modified:
        try:
            parsed = datetime.fromisoformat(archive_modified.replace("Z", "+00:00"))
            return int(parsed.timestamp()), "archive_mtime"
        except ValueError:
            pass

    return None, "unknown"


def _month(capture: int | None) -> str:
    if capture is None:
        return "unknown-date"
    return datetime.fromtimestamp(capture, tz=timezone.utc).strftime("%Y-%m")


def _disambiguate(name: str, crc: int) -> str:
    root, ext = os.path.splitext(name)
    return f"{root}~{crc & 0xFFFFFF:06x}{ext}"


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    media_repo = MediaRepo(ctx.conn)
    scan_repo = ScanRepo(ctx.conn)

    rows = media_repo.all_media()
    if not rows:
        yield ProgressEvent(
            "No media catalogued. Run Scan Archives and Pair Metadata first.",
            progress=1.0,
            level="error",
        )
        return

    media_repo.clear_plan()
    rows = media_repo.all_media()

    existing = scan_repo.drive_file_names()
    geocoder = places.Geocoder(
        ctx.conn, places.api_key_from_env(ctx.config.repo_root)
    )
    if not geocoder.enabled:
        yield ProgressEvent(
            "No GOOGLE_MAPS_API_KEY configured — place tags will be skipped.",
            progress=0.0,
            level="warn",
        )

    taken: set[tuple[str, str]] = set()
    total = len(rows)
    duplicates = placed = unknown_dates = 0

    for index, row in enumerate(rows, start=1):
        if index % 100 == 0 or index == total:
            yield ProgressEvent(f"Planned {index} of {total}.", progress=index / total)

        sidecar = None
        if row["sidecar_id"]:
            sidecar = ctx.conn.execute(
                "SELECT * FROM sidecars WHERE id = ?", (row["sidecar_id"],)
            ).fetchone()

        archive_modified = ctx.conn.execute(
            "SELECT modified_time FROM archives WHERE drive_id = ?",
            (row["archive_drive_id"],),
        ).fetchone()["modified_time"]

        capture, source = resolve_capture(row, sidecar, archive_modified)
        if source == "unknown":
            unknown_dates += 1

        folder = _month(capture)
        name = row["name"]
        if (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        taken.add((folder, name))

        lat = sidecar["latitude"] if sidecar else None
        lon = sidecar["longitude"] if sidecar else None
        place = country = None
        if lat is not None and lon is not None:
            place, country = geocoder.lookup(lat, lon)
            if place:
                placed += 1

        duplicate_of = duplicate_reason = None
        for candidate in existing.get(row["name"], []):
            if candidate["size"] == row["entry_size"]:
                duplicate_of = candidate["parent_path"]
                duplicate_reason = "name and size match an existing file"
                break
            duplicate_of = candidate["parent_path"]
            duplicate_reason = "name matches an existing file, size differs"
        if duplicate_of:
            duplicates += 1

        media_repo.set_plan(
            row["entry_id"],
            capture_time=capture,
            capture_source=source,
            latitude=lat,
            longitude=lon,
            place=place,
            country=country,
            target_folder=folder,
            target_name=name,
            duplicate_of=duplicate_of,
            duplicate_reason=duplicate_reason,
        )

    detail = f"Planned {total} file(s)."
    if duplicates:
        detail += f" {duplicates} already exist in the destination (will still upload)."
    if unknown_dates:
        detail += f" {unknown_dates} have no resolvable date."
    if placed:
        detail += f" {placed} carry a place."
    yield ProgressEvent(detail, progress=1.0)

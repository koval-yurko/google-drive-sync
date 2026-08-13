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

from photolib import buckets, places, takeout
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.geocache_repo import GeocacheRepo
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

ID = "plan_organize"
TITLE = "Plan Organization"
DESCRIPTION = (
    "Resolve a capture date, country, duplicate verdict and destination for every "
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


def _disambiguate(name: str, crc: int) -> str:
    root, ext = os.path.splitext(name)
    return f"{root}~{crc & 0xFFFFFF:06x}{ext}"


def verdict_for(row, verified_by_crc, live_ids, by_name):
    """What Sync should do with this entry: skip, verify at transfer, upload.

    `skip` is certainty bought for nothing: this app uploaded these exact
    bytes and Drive confirmed them, and the file is still there. `verify`
    means a live file of the same name and size exists but its MD5 cannot be
    compared to a ZIP's CRC32 without the bytes, so the decision is deferred
    to the moment the bytes are in hand anyway.
    """
    hit = verified_by_crc.get((row["crc32"], row["entry_size"]))
    if hit is not None and hit["drive_file_id"] in live_ids:
        return "skip", hit["drive_file_id"]

    for candidate in by_name.get(row["name"], []):
        if candidate["drive_id"] not in live_ids:
            continue
        if candidate["size"] == row["entry_size"]:
            return "verify", candidate["drive_id"]

    return "upload", None


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
    verified_by_crc = media_repo.verified_by_crc()
    live_ids = scan_repo.live_drive_ids()
    geocoder = places.Geocoder(
        GeocacheRepo(ctx.conn), places.api_key_from_env(ctx.config.repo_root)
    )
    if not geocoder.enabled:
        yield ProgressEvent(
            "No GOOGLE_MAPS_API_KEY configured — country tags will be skipped.",
            progress=0.0,
            level="warn",
        )

    # Pass 1: resolve every capture, so the packing sees every file's month.
    total = len(rows)
    resolved = []
    for index, row in enumerate(rows, start=1):
        if index % 100 == 0 or index == total:
            yield ProgressEvent(
                f"Resolved {index} of {total}.", progress=index / total / 2
            )

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
        resolved.append((row, sidecar, capture, source))

    # The histogram covers what the library will hold: these rows, plus the
    # legacy Drive files no media row accounts for.
    counts = buckets.unaccounted_drive_months(ctx.conn)
    counts.update(
        month
        for _, _, capture, _ in resolved
        if (month := buckets.month_of(capture)) is not None
    )
    fmap = buckets.folder_map(counts)

    taken: set[tuple[str, str]] = set()
    duplicates = located = unknown_dates = 0
    skipped = to_verify = 0

    for index, (row, sidecar, capture, source) in enumerate(resolved, start=1):
        if index % 100 == 0 or index == total:
            yield ProgressEvent(
                f"Planned {index} of {total}.", progress=0.5 + index / total / 2
            )

        if source == "unknown":
            unknown_dates += 1

        verdict, match = verdict_for(row, verified_by_crc, live_ids, existing)
        if verdict == "skip":
            skipped += 1
        elif verdict == "verify":
            to_verify += 1

        month = buckets.month_of(capture)
        folder = fmap[month] if month else buckets.UNKNOWN_FOLDER
        name = row["name"]
        # A `verify` row's destination name is already occupied by the file it
        # matched. If the MD5s disagree at transfer time this uploads, so it
        # must upload under a free name; if they agree, the name is unused.
        if verdict == "verify" or (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        # Only rows whose target will actually be written may claim a slot.
        # `set_plan` discards target_folder/target_name for a `done` row —
        # its columns record where the file already is — so reserving one
        # here would rename a pending file to dodge a collision that never
        # exists.
        if row["upload_status"] != "done":
            taken.add((folder, name))

        lat = sidecar["latitude"] if sidecar else None
        lon = sidecar["longitude"] if sidecar else None
        country = None
        if lat is not None and lon is not None:
            country = geocoder.lookup(lat, lon)
            if country:
                located += 1

        duplicate_of = duplicate_reason = None
        for candidate in existing.get(row["name"], []):
            if candidate["drive_id"] == row["drive_file_id"]:
                continue  # this file's own verified upload, not a duplicate
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
            country=country,
            target_folder=folder,
            target_name=name,
            duplicate_of=duplicate_of,
            duplicate_reason=duplicate_reason,
            plan_verdict=verdict,
            plan_match=match,
        )

    detail = f"Planned {total} file(s)."
    if skipped:
        detail += f" {skipped} already in the destination (nothing to upload)."
    if to_verify:
        detail += f" {to_verify} will be checked against an existing file."
    if duplicates:
        detail += f" {duplicates} already exist in the destination (will still upload)."
    if unknown_dates:
        detail += f" {unknown_dates} have no resolvable date."
    if located:
        detail += f" {located} carry a country."
    yield ProgressEvent(detail, progress=1.0)

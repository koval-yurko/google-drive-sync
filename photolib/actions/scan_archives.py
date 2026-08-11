"""Index the ZIP archives and the destination folder into the catalog.

Reads only each archive's central directory over byte ranges — a few hundred
kilobytes for a 2.15 GB archive — and never downloads an archive whole.
"""

from __future__ import annotations

from typing import Iterator

from photolib import archives
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE
from photolib.drive.errors import DriveError

ID = "scan_archives"
TITLE = "Scan Archives"
DESCRIPTION = (
    "Index every ZIP archive's contents and the existing contents of the "
    "Global Photos folder. Reads only archive indexes, never whole archives."
)
ORDER = 10


class Params(ActionParams):
    pass


def _index_destination(ctx: ActionContext, folder_id: str) -> int:
    """Walk the destination at any depth and return how many files were seen."""
    rows: list[dict] = []
    stack: list[tuple[str, str]] = [(folder_id, "")]
    while stack:
        current, path = stack.pop()
        for child in ctx.drive.list_children(current):
            if child.is_folder:
                stack.append(
                    (child.id, f"{path}/{child.name}" if path else child.name)
                )
                continue
            rows.append(
                {
                    "drive_id": child.id, "name": child.name,
                    "parent_path": path, "md5": child.md5,
                    "size": child.size, "mime_type": child.mime_type,
                    "capture_hint": child.capture_hint(),
                }
            )
    ScanRepo(ctx.conn).upsert_drive_files(rows)
    return len(rows)


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    repo = ScanRepo(ctx.conn)

    zip_source = ctx.settings.get_folder(ZIP_SOURCE)
    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if zip_source is None or photos_root is None:
        yield ProgressEvent(
            "Both the ZIP source and Global Photos folders must be configured "
            "in Settings before scanning.",
            progress=1.0,
            level="error",
        )
        return

    try:
        children = ctx.drive.list_children(zip_source.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the ZIP source folder: {exc}", progress=1.0, level="error"
        )
        return

    zips = sorted(
        (c for c in children if c.name.lower().endswith(".zip")), key=lambda f: f.name
    )
    if not zips:
        yield ProgressEvent(
            f"No archives found in '{zip_source.name}' — the catalog already "
            "built from them survives. Refreshing the destination index only.",
            progress=0.0,
            level="warn",
        )
    else:
        yield ProgressEvent(f"Found {len(zips)} archive(s).", progress=0.0)

    total = len(zips) + 1

    for index, archive in enumerate(zips, start=1):
        progress = index / total
        if repo.archive_is_current(archive.id, archive.size, archive.modified_time):
            yield ProgressEvent(
                f"{archive.name}: unchanged since last scan, skipping.",
                progress=progress,
            )
            continue

        archive_id = repo.upsert_archive(
            archive.id, archive.name, archive.size, archive.modified_time
        )
        try:
            entries = archives.list_archive_entries(
                ctx.drive, archive.id, archive.size
            )
        except (DriveError, ValueError) as exc:
            yield ProgressEvent(
                f"{archive.name}: cannot read index — {exc}",
                progress=progress,
                level="error",
            )
            continue

        kinds = {e.path: archives.classify(e.path) for e in entries}
        repo.replace_entries(archive_id, entries, kinds)
        repo.mark_indexed(archive_id)

        media = sum(1 for k in kinds.values() if k == archives.MEDIA)
        yield ProgressEvent(
            f"{archive.name}: {len(entries)} entries "
            f"({media} media, {len(entries) - media} sidecars).",
            progress=progress,
        )

    seen = _index_destination(ctx, photos_root.id)
    counts = repo.counts()
    yield ProgressEvent(
        f"Indexed {seen} existing file(s) in '{photos_root.name}'.",
        progress=(total - 0.5) / total,
    )
    yield ProgressEvent(
        f"Scan complete: {counts['media']} media and {counts['sidecars']} "
        f"sidecars across {counts['archives']} archive(s).",
        progress=1.0,
    )

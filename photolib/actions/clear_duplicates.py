"""Trash the redundant identical copies inside the Global Photos folder.

Destructive, so it follows the same rules as Clear Stale Trees:

- it reports by default and does nothing until `confirm` is set;
- the evidence is live — it walks the destination and groups files by the
  MD5 Drive itself reports, never trusting the possibly-stale catalog index;
- one copy of every group always survives. The keeper is a copy this
  pipeline uploaded and verified (recorded in `media.drive_file_id`) when
  there is one, otherwise the first copy by folder and name;
- zero-byte files share an MD5 without being copies of anything. They are
  reported and left alone;
- and it trashes. Nothing is permanently deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "clear_duplicates"
TITLE = "Clear Duplicates"
DESCRIPTION = (
    "Find byte-identical copies inside the Global Photos folder and move all "
    "but one of each to Drive's trash. One copy always survives — a verified "
    "upload when there is one. Reports what it would do unless you confirm."
)
ORDER = 55


class Params(ActionParams):
    confirm: bool = False


def _walk(drive, folder_id: str) -> list[tuple[str, object]]:
    """Every file under a folder, at any depth, with its path for reporting."""
    found, stack = [], [(folder_id, "")]
    while stack:
        current, path = stack.pop()
        for child in drive.list_children(current):
            child_path = f"{path}/{child.name}" if path else child.name
            if child.is_folder:
                stack.append((child.id, child_path))
            else:
                found.append((child_path, child))
    return found


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if photos_root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0,
            level="error",
        )
        return

    try:
        files = _walk(ctx.drive, photos_root.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0,
            level="error",
        )
        return

    empty = [(path, f) for path, f in files if not f.size]
    groups: dict[str, list[tuple[str, object]]] = {}
    for path, file in files:
        if file.md5 and file.size:
            groups.setdefault(file.md5, []).append((path, file))

    verified = {
        row[0]
        for row in ctx.conn.execute(
            "SELECT drive_file_id FROM media WHERE drive_file_id IS NOT NULL"
        )
    }

    redundant: list[tuple[str, object, str]] = []  # path, file, kept path
    for copies in groups.values():
        if len(copies) < 2:
            continue
        copies = sorted(copies, key=lambda c: (c[1].id not in verified, c[0]))
        kept_path = copies[0][0]
        redundant.extend((path, file, kept_path) for path, file in copies[1:])

    freed = sum(f.size or 0 for _, f, _ in redundant)
    yield ProgressEvent(
        f"{len(files)} file(s) scanned: {len(redundant)} redundant cop"
        f"{'y' if len(redundant) == 1 else 'ies'} in "
        f"{sum(1 for c in groups.values() if len(c) > 1)} group(s), "
        f"{freed / 1e9:.2f} GB recoverable.",
        progress=0.1,
    )
    if empty:
        yield ProgressEvent(
            f"{len(empty)} zero-byte file(s) found — broken, not duplicates. "
            "Leaving them alone.",
            progress=0.1,
            level="warn",
        )
    for path, _, kept_path in redundant[:50]:
        yield ProgressEvent(f"would trash: {path} (keeping {kept_path})",
                            progress=0.1)

    if not redundant:
        yield ProgressEvent("No duplicates. Nothing to do.", progress=1.0)
        return

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — nothing was changed. Re-run with confirm to move "
            f"{len(redundant)} cop{'y' if len(redundant) == 1 else 'ies'} to "
            "Drive's trash.",
            progress=1.0,
            level="warn",
        )
        return

    stamp = datetime.now(timezone.utc).isoformat()
    trashed = 0
    for index, (path, file, _) in enumerate(redundant, start=1):
        try:
            ctx.writer.trash(file.id)
        except DriveError as exc:
            yield ProgressEvent(f"{path}: {exc}", level="error")
            continue
        ctx.conn.execute(
            "UPDATE drive_files SET trashed_at = ? WHERE drive_id = ?",
            (stamp, file.id),
        )
        ctx.conn.commit()
        trashed += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Trashed {index} of {len(redundant)}.",
                progress=0.1 + 0.9 * index / len(redundant),
            )

    yield ProgressEvent(
        f"Moved {trashed} cop{'y' if trashed == 1 else 'ies'} to Drive's "
        f"trash, freeing {freed / 1e9:.2f} GB. One copy of every file "
        "remains, and the trash is recoverable until it is emptied.",
        progress=1.0,
    )

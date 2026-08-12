"""Move every live file into its ~100-file bucket folder, then tidy up.

Metadata-only: files are reparented with one `files.update` each — no bytes
are downloaded or re-uploaded. The same call renames arrivals that would
collide and strips the retired `place` property. Folders left empty are
trashed, never deleted.

Shaped like `sync_tags`: it reports everything it would do and changes
nothing until you confirm. Re-running after new uploads is expected — the
packing shifts as months fill, and reconciling is cheap.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Iterator

from photolib import buckets
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "reorganize"
TITLE = "Repack Buckets"
DESCRIPTION = (
    "Move every indexed file into its ~100-file bucket folder (whole months, "
    "packed greedily), renaming on collisions, clearing the retired place "
    "property, and trashing folders left empty. Reports what it would do "
    "unless you confirm."
)
ORDER = 45


class Params(ActionParams):
    confirm: bool = False


def _folder_paths(drive, root_id: str) -> dict[str, str]:
    """Every folder path under the root, mapped to its id. '' is the root."""
    paths = {"": root_id}
    stack: list[tuple[str, str]] = [(root_id, "")]
    while stack:
        current, path = stack.pop()
        for child in drive.list_children(current, folders_only=True):
            child_path = f"{path}/{child.name}" if path else child.name
            paths[child_path] = child.id
            stack.append((child.id, child_path))
    return paths


def _sweep_empty(drive, writer, folder_id: str) -> int:
    """Trash child folders that hold nothing, depth first. Never the root."""
    swept = 0
    for child in drive.list_children(folder_id):
        if not child.is_folder:
            continue
        swept += _sweep_empty(drive, writer, child.id)
        if not drive.list_children(child.id):
            writer.trash(child.id)
            swept += 1
    return swept


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

    rows = list(ctx.conn.execute(
        "SELECT d.drive_id, d.name, d.parent_path, d.md5, m.id AS media_id, "
        "       CASE WHEN m.id IS NULL THEN d.capture_hint "
        "            ELSE m.capture_time END AS capture "
        "FROM drive_files d LEFT JOIN media m ON m.drive_file_id = d.drive_id "
        "WHERE d.trashed_at IS NULL ORDER BY d.parent_path, d.name"
    ))
    if not rows:
        yield ProgressEvent(
            "Nothing indexed. Run Scan Archives first.", progress=1.0,
            level="error",
        )
        return

    fmap = buckets.folder_map(buckets.library_histogram(ctx.conn))
    targets: dict[str, str] = {}
    # Names already resident per target folder, so arrivals can dodge them.
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        month = buckets.month_of(row["capture"])
        target = fmap[month] if month else buckets.UNKNOWN_FOLDER
        targets[row["drive_id"]] = target
        if target == row["parent_path"]:
            names[target].add(row["name"])

    moves = [row for row in rows if targets[row["drive_id"]] != row["parent_path"]]

    per_folder = Counter(targets[row["drive_id"]] for row in rows)
    yield ProgressEvent(
        f"{len(moves)} of {len(rows)} file(s) would move, filling "
        f"{len(per_folder)} folder(s).",
        progress=0.1,
    )
    for folder in sorted(per_folder):
        yield ProgressEvent(f"{folder}: {per_folder[folder]} file(s)")
    for row in moves[:50]:
        yield ProgressEvent(
            f"would move {row['parent_path']}/{row['name']} "
            f"-> {targets[row['drive_id']]}"
        )

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — nothing was changed. Re-run with confirm to move "
            f"{len(moves)} file(s) and sweep empty folders.",
            progress=1.0,
            level="warn",
        )
        return

    try:
        folder_ids = _folder_paths(ctx.drive, photos_root.id)
        # ensure_folder must run sequentially: Drive would happily create the
        # same folder twice.
        for name in sorted({targets[row["drive_id"]] for row in moves}):
            if name not in folder_ids:
                folder_ids[name] = ctx.writer.ensure_folder(
                    photos_root.id, name
                ).id
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot prepare destination folders: {exc}", progress=1.0,
            level="error",
        )
        return

    moved = failed = renamed = 0
    for index, row in enumerate(moves, start=1):
        target = targets[row["drive_id"]]
        name = row["name"]
        if name in names[target]:
            stem, ext = os.path.splitext(name)
            name = f"{stem}~{(row['md5'] or row['drive_id'])[:6]}{ext}"
            renamed += 1
        try:
            old_parent = folder_ids.get(row["parent_path"])
            if old_parent is None:
                parents = ctx.drive.get_file(row["drive_id"]).parents
                old_parent = parents[0] if parents else photos_root.id
            ctx.writer.move(
                row["drive_id"],
                add_parent=folder_ids[target],
                remove_parent=old_parent,
                name=None if name == row["name"] else name,
                properties={"place": None},
            )
        except DriveError as exc:
            failed += 1
            yield ProgressEvent(f"{row['name']}: {exc}", level="error")
            continue
        names[target].add(name)
        ctx.conn.execute(
            "UPDATE drive_files SET parent_path = ?, name = ? WHERE drive_id = ?",
            (target, name, row["drive_id"]),
        )
        ctx.conn.execute(
            "UPDATE media SET target_folder = ?, target_name = ? "
            "WHERE drive_file_id = ?",
            (target, name, row["drive_id"]),
        )
        ctx.conn.commit()
        moved += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Moved {index} of {len(moves)}.",
                progress=0.1 + 0.7 * index / len(moves),
            )

    # Unmoved catalogued files still carry the property Organize once wrote.
    # A blind clear costs one call each and is a no-op where it is absent.
    cleared = 0
    for row in rows:
        if row["media_id"] is None or targets[row["drive_id"]] != row["parent_path"]:
            continue
        try:
            ctx.writer.update_properties(row["drive_id"], {"place": None})
            cleared += 1
        except DriveError as exc:
            yield ProgressEvent(f"{row['name']}: {exc}", level="error")

    try:
        swept = _sweep_empty(ctx.drive, ctx.writer, photos_root.id)
    except DriveError as exc:
        swept = 0
        yield ProgressEvent(f"Sweep stopped early: {exc}", level="error")

    detail = f"Moved {moved} file(s) into bucket folders."
    if renamed:
        detail += f" {renamed} renamed to avoid collisions."
    if cleared:
        detail += f" Cleared the retired place property from {cleared} file(s)."
    if swept:
        detail += f" Trashed {swept} empty folder(s)."
    if failed:
        detail += f" {failed} failed — re-run to retry them."
    yield ProgressEvent(detail, progress=1.0, level="warn" if failed else "info")

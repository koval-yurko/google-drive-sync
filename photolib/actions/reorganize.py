"""Move every live file into its ~100-file bucket folder, then tidy up.

Shaped like `sync_tags`: it reports everything it would do and changes
nothing until you confirm. Re-running after new uploads is expected — the
packing shifts as months fill, and reconciling is cheap.

See `photolib.repack` for how the plan itself — the bucket-diff, the
collision renames, and the empty-folder sweep — is worked out.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterator

from photolib import repack
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

    rows, targets, names = repack.targets_for(ctx.conn)
    if not rows:
        yield ProgressEvent(
            "Nothing indexed. Run Scan Archives first.", progress=1.0,
            level="error",
        )
        return

    moves = repack.moves_from_targets(rows, targets, names)

    per_folder = Counter(targets[row["drive_id"]] for row in rows)
    yield ProgressEvent(
        f"{len(moves)} of {len(rows)} file(s) would move, filling "
        f"{len(per_folder)} folder(s).",
        progress=0.1,
    )
    for folder in sorted(per_folder):
        yield ProgressEvent(f"{folder}: {per_folder[folder]} file(s)")
    for move in moves[:50]:
        yield ProgressEvent(
            f"would move {move.from_path}/{move.name} -> {move.to_folder}"
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
        folder_ids = repack.folder_paths(ctx.drive, photos_root.id)
        # ensure_folder must run sequentially: Drive would happily create the
        # same folder twice.
        target_names = sorted({move.to_folder for move in moves})
        folder_ids.update(
            repack.ensure_folders(ctx.writer, photos_root.id, target_names)
        )
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot prepare destination folders: {exc}", progress=1.0,
            level="error",
        )
        return

    moved = failed = renamed = 0
    for index, move in enumerate(moves, start=1):
        if move.new_name != move.name:
            renamed += 1
        try:
            move_folder_ids = folder_ids
            if move.from_path not in folder_ids:
                # A stale or unresolved parent_path: fall back to Drive's own
                # record of this one file's current parent, exactly as this
                # row would resolve it — not cached for any other row, since
                # a shared parent_path is not a guarantee of a shared parent.
                parents = ctx.drive.get_file(move.drive_id).parents
                old_parent = parents[0] if parents else photos_root.id
                move_folder_ids = {**folder_ids, move.from_path: old_parent}
            repack.apply_move(
                ctx.writer, ctx.conn, move, move_folder_ids, drive=ctx.drive
            )
        except DriveError as exc:
            failed += 1
            yield ProgressEvent(f"{move.name}: {exc}", level="error")
            continue
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
        to_sweep = repack.plan_sweep(ctx.drive, photos_root.id)
        swept = 0
        for folder_id, _name in to_sweep:
            repack.apply_sweep(ctx.writer, folder_id)
            swept += 1
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

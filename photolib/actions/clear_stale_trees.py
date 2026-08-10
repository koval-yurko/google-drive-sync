"""Trash the redundant extracted Takeout trees, once their contents are safe.

The only destructive action in the project, and deliberately awkward:

- it reports by default and does nothing until `confirm` is set;
- a file is eligible only when a media row of the same name is `done` with a
  Drive-confirmed MD5 — the evidence Organize recorded, not a fresh guess;
- the tree is named explicitly rather than inferred, because a wrong guess
  here is not recoverable by clicking again;
- it refuses to run against the destination folder;
- and it trashes. Nothing is permanently deleted, and the source archives are
  never touched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "clear_stale_trees"
TITLE = "Clear Stale Trees"
DESCRIPTION = (
    "Move an extracted Takeout tree to Drive's trash, one file at a time, and "
    "only where the same file has already been uploaded and verified. Reports "
    "what it would do unless you confirm."
)
ORDER = 50


class Params(ActionParams):
    tree_folder_id: str = ""
    confirm: bool = False


def _walk(drive, folder_id: str) -> list:
    """Every file under a folder, at any depth."""
    files, stack = [], [folder_id]
    while stack:
        for child in drive.list_children(stack.pop()):
            if child.is_folder:
                stack.append(child.id)
            else:
                files.append(child)
    return files


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    if not params.tree_folder_id:
        yield ProgressEvent(
            "Name the extracted tree to clear by its Drive folder id. This "
            "action never guesses which folder you meant.",
            progress=1.0,
            level="error",
        )
        return

    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if photos_root and params.tree_folder_id == photos_root.id:
        yield ProgressEvent(
            "That is the Global Photos folder — the destination, not a stale "
            "tree. Refusing.",
            progress=1.0,
            level="error",
        )
        return

    try:
        files = _walk(ctx.drive, params.tree_folder_id)
    except DriveError as exc:
        yield ProgressEvent(f"Cannot read that folder: {exc}", progress=1.0,
                            level="error")
        return

    if not files:
        yield ProgressEvent("That folder holds no files.", progress=1.0)
        return

    verified = MediaRepo(ctx.conn).uploaded_by_name()
    eligible, ineligible = [], []
    for file in files:
        if file.name in verified:
            eligible.append(file)
        else:
            ineligible.append(file)

    freed = sum(f.size or 0 for f in eligible)
    yield ProgressEvent(
        f"{len(eligible)} eligible, {len(ineligible)} ineligible, "
        f"{freed / 1e9:.2f} GB recoverable.",
        progress=0.1,
    )
    for file in eligible[:50]:
        yield ProgressEvent(f"would trash: {file.name}", progress=0.1)
    for file in ineligible[:50]:
        yield ProgressEvent(
            f"keeping {file.name}: no verified upload of that name",
            progress=0.1,
            level="warn",
        )

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — nothing was changed. Re-run with confirm to move "
            f"{len(eligible)} file(s) to Drive's trash.",
            progress=1.0,
            level="warn",
        )
        return

    stamp = datetime.now(timezone.utc).isoformat()
    trashed = 0
    for index, file in enumerate(eligible, start=1):
        try:
            ctx.writer.trash(file.id)
        except DriveError as exc:
            yield ProgressEvent(f"{file.name}: {exc}", level="error")
            continue
        ctx.conn.execute(
            "UPDATE drive_files SET trashed_at = ? WHERE drive_id = ?",
            (stamp, file.id),
        )
        ctx.conn.commit()
        trashed += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Trashed {index} of {len(eligible)}.",
                progress=0.1 + 0.9 * index / len(eligible),
            )

    yield ProgressEvent(
        f"Moved {trashed} file(s) to Drive's trash, freeing {freed / 1e9:.2f} GB. "
        "They remain recoverable from the trash until it is emptied.",
        progress=1.0,
    )

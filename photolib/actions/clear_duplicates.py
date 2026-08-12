"""Trash the redundant identical copies inside the Global Photos folder.

Destructive, so it follows the same rules as Clear Stale Trees:

- it reports by default and does nothing until `confirm` is set;
- and it trashes. Nothing is permanently deleted.

See `photolib.dedupe` for how the plan itself — which copies are
redundant, and which copy of each group survives — is worked out.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from photolib import dedupe
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


def _path_of(name: str, parent_path: str) -> str:
    return f"{parent_path}/{name}" if parent_path else name


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
        removals, zero, total = dedupe.plan_removals(
            ctx.drive, ctx.conn, photos_root.id
        )
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0,
            level="error",
        )
        return

    groups = len({r.keeper_id for r in removals})
    freed = sum(r.size for r in removals)
    yield ProgressEvent(
        f"{total} file(s) scanned: {len(removals)} redundant cop"
        f"{'y' if len(removals) == 1 else 'ies'} in "
        f"{groups} group(s), "
        f"{freed / 1e9:.2f} GB recoverable.",
        progress=0.1,
    )
    if zero:
        yield ProgressEvent(
            f"{len(zero)} zero-byte file(s) found — broken, not duplicates. "
            "Leaving them alone.",
            progress=0.1,
            level="warn",
        )
    for r in removals[:50]:
        yield ProgressEvent(
            f"would trash: {_path_of(r.name, r.parent_path)} "
            f"(keeping {r.keeper_path})",
            progress=0.1,
        )

    if not removals:
        yield ProgressEvent("No duplicates. Nothing to do.", progress=1.0)
        return

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — nothing was changed. Re-run with confirm to move "
            f"{len(removals)} cop{'y' if len(removals) == 1 else 'ies'} to "
            "Drive's trash.",
            progress=1.0,
            level="warn",
        )
        return

    stamp = datetime.now(timezone.utc).isoformat()
    trashed = 0
    for index, r in enumerate(removals, start=1):
        try:
            dedupe.apply_removal(ctx.writer, r, ctx.conn, stamp)
        except DriveError as exc:
            yield ProgressEvent(
                f"{_path_of(r.name, r.parent_path)}: {exc}", level="error"
            )
            continue
        trashed += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Trashed {index} of {len(removals)}.",
                progress=0.1 + 0.9 * index / len(removals),
            )

    yield ProgressEvent(
        f"Moved {trashed} cop{'y' if trashed == 1 else 'ies'} to Drive's "
        f"trash, freeing {freed / 1e9:.2f} GB. One copy of every file "
        "remains, and the trash is recoverable until it is emptied.",
        progress=1.0,
    )

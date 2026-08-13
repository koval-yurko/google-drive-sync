"""What the catalog believes, checked against what Drive actually holds.

Read-only and prescriptive of nothing. It answers one question — has anything
drifted since the last run? — and leaves the fixing to the flows.

It walks Drive live rather than trusting `drive_files`, because a stale index
is exactly one of the things it exists to find.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "verify_library"
TITLE = "Verify Library"
DESCRIPTION = (
    "Compare the catalog against what Drive actually holds and report every "
    "difference: files deleted or moved outside the app, MD5 mismatches, "
    "uploads never confirmed, and tags pointing at files that are gone. "
    "Writes nothing."
)
ORDER = 90
GROUP = "tool"

EXAMPLES = 20


class Params(ActionParams):
    pass


def _walk(drive, folder_id: str) -> dict[str, tuple[str, str | None]]:
    """Live files under `folder_id` as {drive_id: (parent_path, md5)}."""
    found: dict[str, tuple[str, str | None]] = {}
    stack = [(folder_id, "")]
    while stack:
        current, path = stack.pop()
        for child in drive.list_children(current):
            if child.is_folder:
                stack.append(
                    (child.id, f"{path}/{child.name}" if path else child.name)
                )
                continue
            found[child.id] = (path, child.md5)
    return found


def _report(label: str, rows: list[str], progress: float) -> ProgressEvent:
    head = ", ".join(rows[:EXAMPLES])
    more = f" (+{len(rows) - EXAMPLES} more)" if len(rows) > EXAMPLES else ""
    return ProgressEvent(
        f"{len(rows)} {label}: {head}{more}", progress=progress, level="warn"
    )


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    root = ctx.settings.get_folder(PHOTOS_ROOT)
    if root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0, level="error",
        )
        return

    try:
        live = _walk(ctx.drive, root.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0, level="error",
        )
        return

    yield ProgressEvent(f"Drive holds {len(live)} file(s).", progress=0.3)

    uploaded = list(ctx.conn.execute(
        "SELECT m.drive_file_id, m.md5, m.target_folder, m.target_name, e.name "
        "FROM media m JOIN entries e ON e.id = m.entry_id "
        "WHERE m.upload_status = 'done' "
        "ORDER BY e.name"
    ))

    # `missing` and `unconfirmed` are not mutually exclusive: a row with a
    # real `drive_file_id` but no confirmed `md5` still gets the live lookup,
    # so it can land in both lists at once. `MediaRepo.mark_uploaded` always
    # sets `drive_file_id` and `md5` together, so this split should never
    # happen in normal use — but catching exactly the anomalies the app
    # itself would never produce is this action's whole job, so the two
    # problems (gone vs. never confirmed) must not be collapsed into one.
    missing, moved, mismatched, unconfirmed = [], [], [], []
    for row in uploaded:
        if row["drive_file_id"] is None:
            unconfirmed.append(row["name"])
            continue
        found = live.get(row["drive_file_id"])
        if row["md5"] is None:
            if found is None:
                missing.append(row["name"])
            unconfirmed.append(row["name"])
            continue
        if found is None:
            missing.append(row["name"])
            continue
        parent_path, md5 = found
        if parent_path != row["target_folder"]:
            moved.append(f"{row['name']} → {parent_path}")
        if md5 is not None and md5 != row["md5"]:
            mismatched.append(row["name"])

    orphans = [
        r["drive_id"] for r in ctx.conn.execute(
            "SELECT DISTINCT ft.drive_id FROM file_tags ft "
            "LEFT JOIN drive_files d ON d.drive_id = ft.drive_id "
            "WHERE d.drive_id IS NULL "
            "ORDER BY ft.drive_id"
        )
    ]

    categories = (
        ("file(s) recorded as uploaded are no longer in Drive", missing),
        ("file(s) were moved outside the app", moved),
        ("file(s) have an MD5 Drive disagrees with", mismatched),
        ("upload(s) are marked done but were never confirmed", unconfirmed),
        ("tagged file(s) no longer exist", orphans),
    )

    drift = False
    for index, (label, rows) in enumerate(categories, start=1):
        if not rows:
            continue
        drift = True
        yield _report(label, rows, 0.3 + 0.7 * index / len(categories))

    if not drift:
        yield ProgressEvent(
            f"No drift. {len(uploaded)} verified upload(s) all present, in "
            "place and matching.",
            progress=1.0,
        )
    else:
        yield ProgressEvent("Nothing was changed.", progress=1.0)

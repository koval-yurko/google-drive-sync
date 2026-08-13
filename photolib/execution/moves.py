"""Enacting a folder-layout plan: reparent a file, make a bucket folder,
trash a folder left empty.

Every function here takes a writer. `photolib.planning.layout` decides
what these should be called with.
"""

from __future__ import annotations

from photolib.db.layout_repo import LayoutRepo
from photolib.drive.errors import DriveError
from photolib.planning.layout import Move


def apply_move(
    writer, conn, move: Move, folder_ids: dict[str, str], drive=None
) -> None:
    """Reparent one file to its planned bucket and record the new location.

    Replay hazard: a resumed run can call this a second time for a move
    whose Drive effect already landed on an earlier attempt — see
    `photolib.db.job_items_repo`'s module docstring for why. On that second
    call `remove_parent` names a parent the file no longer has. The Drive
    v3 `files.update` reference
    (https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update)
    documents what `addParents`/`removeParents` do but not what happens when
    the named parent is already absent, and no other authoritative source
    settles it either way. Rather than gamble on undocumented behaviour,
    a `DriveError` from the move is treated as success — not re-raised — when
    `drive` confirms the file already sits in the target folder, which is
    the state this call was trying to reach regardless of why the API call
    itself failed. Passing `drive=None` (the default) restores the old,
    unguarded behaviour.
    """
    try:
        writer.move(
            move.drive_id,
            add_parent=folder_ids[move.to_folder],
            remove_parent=folder_ids[move.from_path],
            name=None if move.new_name == move.name else move.new_name,
            properties={"place": None},
        )
    except DriveError:
        already_moved = (
            drive is not None
            and folder_ids[move.to_folder]
            in drive.get_file(move.drive_id).parents
        )
        if not already_moved:
            raise
    LayoutRepo(conn).record_move(move.drive_id, move.to_folder, move.new_name)


def ensure_folders(writer, root_id: str, folders: list[str]) -> dict[str, str]:
    """Find or create each named bucket folder directly under the root.

    Must run sequentially: Drive would happily create the same folder
    twice, so calling this concurrently could silently split a month
    across two folders.
    """
    return {name: writer.ensure_folder(root_id, name).id for name in folders}


def apply_sweep(writer, folder_id: str) -> None:
    """Trash one folder found empty by `plan_sweep`.

    Safe to replay for the same reason as
    `photolib.execution.trash.apply_removal`: Drive keeps a trashed item
    retrievable for 30 days
    (https://developers.google.com/workspace/drive/api/guides/delete), so
    trashing an already-trashed folder is not an error.
    """
    writer.trash(folder_id)

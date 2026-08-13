"""Trashing a redundant copy the duplicate plan named.

Takes a writer. `photolib.planning.duplicates` decides what to trash.
"""

from __future__ import annotations

from datetime import datetime, timezone

from photolib.db.scan_repo import ScanRepo
from photolib.planning.duplicates import Removal


def apply_removal(writer, removal: Removal, conn) -> None:
    """Trash the redundant copy and stamp it trashed in the catalog.

    Safe to replay: Drive's trash guide says a trashed file stays retrievable
    by `files.get` — and therefore still patchable — until it is
    auto-deleted 30 days later
    (https://developers.google.com/workspace/drive/api/guides/delete), so
    setting `trashed: true` on a file that is already trashed is just
    resetting a field to the value it already has, not an error. The
    `UPDATE` below is an ordinary overwrite, harmless to repeat.
    """
    writer.trash(removal.drive_id)
    stamp = datetime.now(timezone.utc).isoformat()
    ScanRepo(conn).mark_trashed(removal.drive_id, stamp)

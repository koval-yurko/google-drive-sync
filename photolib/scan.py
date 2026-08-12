"""The destination walk, shared by Scan Archives and Reorganize Folders.

The whole tree is collected before anything is written, because
`ScanRepo.upsert_drive_files` ends with a sweep that deletes every row not
carrying that call's timestamp — that sweep is how a file deleted from Drive
leaves the catalog, and it is only correct after a complete walk. Calling it
per folder would delete each folder's rows as the next folder arrived.

That also means the walk cannot be resumed part-way: a partial index would
sweep away everything it had not yet reached. It does not need to be. Listing
a folder is one API call and the walk is read-only and idempotent, so a
re-walk costs seconds; the caller checkpoints it as a single unit.
"""

from __future__ import annotations

from photolib.db.scan_repo import ScanRepo


def index_destination(drive, conn, folder_id: str) -> int:
    """Walk `folder_id` at any depth, upsert every file, return the count."""
    rows: list[dict] = []
    stack: list[tuple[str, str]] = [(folder_id, "")]
    seen: set[str] = set()

    while stack:
        current, path = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        for child in drive.list_children(current):
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

    ScanRepo(conn).upsert_drive_files(rows)
    return len(rows)

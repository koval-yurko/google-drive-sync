"""Plan which redundant identical copies inside the Global Photos folder to trash.

- the evidence is live — it walks the destination and groups files by the
  MD5 Drive itself reports, never trusting the possibly-stale catalog index;
- one copy of every group always survives. The keeper is a copy this
  pipeline uploaded and verified (recorded in `media.drive_file_id`) when
  there is one, otherwise the first copy by folder and name;
- zero-byte files share an MD5 without being copies of anything. They are
  reported and left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Removal:
    drive_id: str
    name: str
    parent_path: str
    md5: str
    keeper_id: str
    keeper_path: str


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


def plan_removals(drive, conn, root_id: str) -> tuple[list[Removal], list[str]]:
    """Group the live files under `root_id` by MD5 and decide what to trash.

    Returns the removals (one entry per redundant copy, naming the keeper
    that survives it) and the drive ids of zero-byte files that were found
    but left alone — they share an MD5 without being real copies.
    """
    files = _walk(drive, root_id)

    empty_ids = [file.id for _, file in files if not file.size]

    groups: dict[str, list[tuple[str, object]]] = {}
    for path, file in files:
        if file.md5 and file.size:
            groups.setdefault(file.md5, []).append((path, file))

    verified = {
        row[0]
        for row in conn.execute(
            "SELECT drive_file_id FROM media WHERE drive_file_id IS NOT NULL"
        )
    }

    removals: list[Removal] = []
    for copies in groups.values():
        if len(copies) < 2:
            continue
        copies = sorted(copies, key=lambda c: (c[1].id not in verified, c[0]))
        keeper_path, keeper_file = copies[0]
        for path, file in copies[1:]:
            parent_path, _, name = path.rpartition("/")
            removals.append(Removal(
                drive_id=file.id,
                name=name,
                parent_path=parent_path,
                md5=file.md5,
                keeper_id=keeper_file.id,
                keeper_path=keeper_path,
            ))

    return removals, empty_ids


def apply_removal(writer, removal: Removal, conn) -> None:
    """Trash the redundant copy and stamp it trashed in the catalog."""
    writer.trash(removal.drive_id)
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE drive_files SET trashed_at = ? WHERE drive_id = ?",
        (stamp, removal.drive_id),
    )
    conn.commit()

"""Plan moving every live file into its ~100-file bucket folder, and the
sweep of folders left empty afterward.

Metadata-only: a move is one `files.update` per file — no bytes are
downloaded or re-uploaded. The same call renames arrivals that would
collide inside their destination folder and strips the retired `place`
property. Folders left empty are trashed, never deleted.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass

from photolib import buckets

FOLDER_QUERY = (
    "SELECT d.drive_id, d.name, d.parent_path, d.md5, m.id AS media_id, "
    "       CASE WHEN m.id IS NULL THEN d.capture_hint "
    "            ELSE m.capture_time END AS capture "
    "FROM drive_files d LEFT JOIN media m ON m.drive_file_id = d.drive_id "
    "WHERE d.trashed_at IS NULL ORDER BY d.parent_path, d.name"
)


@dataclass
class Move:
    drive_id: str
    name: str
    new_name: str
    from_path: str
    to_folder: str


def _histogram(conn, exclude: set[str]) -> Counter[str]:
    """`buckets.library_histogram`, minus files about to be trashed.

    Mirrors its two sources (unaccounted live Drive files, and every
    catalogued media row) exactly, but drops `exclude`d drive ids from each
    before counting, so a file dedupe is about to remove does not reserve
    space in the bucket its month would otherwise need.
    """
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT d.drive_id, d.capture_hint FROM drive_files d "
        "LEFT JOIN media m ON m.drive_file_id = d.drive_id "
        "WHERE d.trashed_at IS NULL AND m.id IS NULL"
    ):
        if row["drive_id"] in exclude:
            continue
        month = buckets.month_of(row["capture_hint"])
        if month is not None:
            counts[month] += 1
    for row in conn.execute("SELECT drive_file_id, capture_time FROM media"):
        if row["drive_file_id"] in exclude:
            continue
        month = buckets.month_of(row["capture_time"])
        if month is not None:
            counts[month] += 1
    return counts


def targets_for(conn, exclude: set[str] = frozenset()):
    """Every live catalogued file's bucket target, plus which names already
    sit in each target folder so an arrival can dodge them.

    Public — not just an implementation detail of `plan_moves` — because an
    action reporting the full picture (including files that already sit
    where they belong, not just the ones that must move) needs the same
    `rows`/`targets` this produces. It is one SQL query and some in-memory
    bucket packing, not another live Drive traversal, so both `plan_moves`
    and that caller sharing it costs nothing extra worth avoiding by
    duplicating the bucket-diff logic instead.
    """
    rows = [
        row for row in conn.execute(FOLDER_QUERY)
        if row["drive_id"] not in exclude
    ]
    fmap = buckets.folder_map(_histogram(conn, exclude))
    targets: dict[str, str] = {}
    # Names already resident per target folder, so arrivals can dodge them.
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        month = buckets.month_of(row["capture"])
        target = fmap[month] if month else buckets.UNKNOWN_FOLDER
        targets[row["drive_id"]] = target
        if target == row["parent_path"]:
            names[target].add(row["name"])
    return rows, targets, names


def moves_from_targets(rows, targets, names) -> list[Move]:
    """Build the Move list from a `targets_for` computation.

    Renames as needed to avoid colliding with a file already at the
    destination or with another move landing there first. Split out of
    `plan_moves` so a caller that already has a `targets_for` result (to
    report on the full library, not just what must move) can build the
    move list from it without re-running the query and the bucket packing.
    """
    moves: list[Move] = []
    for row in rows:
        target = targets[row["drive_id"]]
        if target == row["parent_path"]:
            continue
        name = row["name"]
        if name in names[target]:
            stem, ext = os.path.splitext(name)
            name = f"{stem}~{(row['md5'] or row['drive_id'])[:6]}{ext}"
        moves.append(Move(
            drive_id=row["drive_id"], name=row["name"], new_name=name,
            from_path=row["parent_path"], to_folder=target,
        ))
        names[target].add(name)
    return moves


def plan_moves(
    drive, conn, root_id: str, *, exclude: set[str] = frozenset()
) -> list[Move]:
    """Every live catalogued file whose bucket target differs from where it
    currently sits, renamed as needed to avoid colliding with a file
    already at that destination or with another move landing there first.

    `exclude` drops files dedupe is about to trash from consideration and
    from the space they would otherwise reserve — see `_histogram`.
    """
    rows, targets, names = targets_for(conn, exclude)
    return moves_from_targets(rows, targets, names)


def apply_move(writer, conn, move: Move, folder_ids: dict[str, str]) -> None:
    """Reparent one file to its planned bucket and record the new location."""
    writer.move(
        move.drive_id,
        add_parent=folder_ids[move.to_folder],
        remove_parent=folder_ids[move.from_path],
        name=None if move.new_name == move.name else move.new_name,
        properties={"place": None},
    )
    conn.execute(
        "UPDATE drive_files SET parent_path = ?, name = ? WHERE drive_id = ?",
        (move.to_folder, move.new_name, move.drive_id),
    )
    conn.execute(
        "UPDATE media SET target_folder = ?, target_name = ? "
        "WHERE drive_file_id = ?",
        (move.to_folder, move.new_name, move.drive_id),
    )
    conn.commit()


def folder_paths(drive, root_id: str) -> dict[str, str]:
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


def ensure_folders(writer, root_id: str, folders: list[str]) -> dict[str, str]:
    """Find or create each named bucket folder directly under the root.

    Must run sequentially: Drive would happily create the same folder
    twice, so calling this concurrently could silently split a month
    across two folders.
    """
    return {name: writer.ensure_folder(root_id, name).id for name in folders}


def plan_sweep(drive, root_id: str) -> list[tuple[str, str]]:
    """Folders under the root that hold nothing, depth first. Never the root.

    A folder counts as empty once its own folder children — recursively —
    would also be swept; a folder holding a live file, or a folder child
    that isn't itself fully empty, is never listed.
    """
    swept: list[tuple[str, str]] = []

    def _visit(folder_id: str) -> bool:
        empty = True
        for child in drive.list_children(folder_id):
            if not child.is_folder:
                empty = False
                continue
            if _visit(child.id):
                swept.append((child.id, child.name))
            else:
                empty = False
        return empty

    _visit(root_id)
    return swept


def apply_sweep(writer, folder_id: str) -> None:
    """Trash one folder found empty by `plan_sweep`."""
    writer.trash(folder_id)

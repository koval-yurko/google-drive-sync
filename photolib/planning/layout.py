"""Where every live file belongs: its bucket folder, and the folders left
empty once everything has moved.

Planning only — nothing here mutates Drive. `photolib.execution.moves`
enacts what these functions decide.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

from photolib.db.layout_repo import LayoutRepo
from photolib.planning import buckets


@dataclass
class Move:
    drive_id: str
    name: str
    new_name: str
    from_path: str
    to_folder: str


def targets_for(conn, exclude: set[str] = frozenset()):
    """Every live catalogued file's bucket target, plus which names already
    sit in each target folder so an arrival can dodge them.

    Public — not just an implementation detail of `plan_moves` — because an
    action reporting the full picture (including files that already sit
    where they belong, not just the ones that must move) needs the same
    `rows`/`targets` this produces.
    """
    repo = LayoutRepo(conn)
    rows = repo.live_files_for_layout(exclude)
    fmap = buckets.folder_map(repo.capture_histogram(exclude))
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
    from the space they would otherwise reserve — see
    `LayoutRepo.capture_histogram`.
    """
    rows, targets, names = targets_for(conn, exclude)
    return moves_from_targets(rows, targets, names)


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

"""Manual tags and their assignment to Drive files.

Tags key on Drive's own file id rather than `drive_files.id`, because Scan
rebuilds that table and its autoincrement ids move. That also means a tag
survives the file leaving and returning, and that `file_tags` may hold rows
for files no longer in Drive — those are retained, not pruned, and excluded
from every count.

Every tag here is manual. Country, month, media type and duplicate status are
derived from columns at query time (see `library_repo`), never stored as rows.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

DEFAULT_COLOR = "#6b7280"

# `t_<slug>` and its value share Drive's 124-byte appProperties budget.
MAX_SLUG = 60

# SQLite's default host-parameter ceiling is 999. A page of 1,284 files would
# blow straight through it, so anything taking a list of ids batches.
_BATCH = 400


class DuplicateTagError(Exception):
    """That name already exists, or slugs onto one that does."""


def slugify(name: str) -> str:
    """A URL-safe, Drive-property-safe key for a tag name."""
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower())
    return hyphenated.strip("-")[:MAX_SLUG].strip("-")


def _batched(items: list[str]) -> list[list[str]]:
    return [items[i : i + _BATCH] for i in range(0, len(items), _BATCH)]


class TagsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # Shared with every other repo over this connection (see
        # catalog.LockedConnection). Individual statements serialise
        # themselves; this is for sequences that must land as a unit and for
        # reads that iterate a cursor instead of materialising it.
        self._lock = conn.lock

    # ---------- the tags themselves ----------

    def create(self, name: str, color: str = DEFAULT_COLOR) -> sqlite3.Row:
        name = name.strip()
        slug = slugify(name)
        if not slug:
            raise ValueError("a tag name must contain at least one letter or digit")
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO tags (name, slug, color) VALUES (?, ?, ?)",
                    (name, slug, color),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateTagError(
                    f"a tag named '{slug}' already exists"
                ) from exc
            self._conn.commit()
            return self.get(cursor.lastrowid)

    def get(self, tag_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()

    def list_with_counts(self) -> list[sqlite3.Row]:
        """Every tag, with how many live Drive files carry it."""
        return list(
            self._conn.execute(
                "SELECT t.id, t.name, t.slug, t.color, "
                "       (SELECT COUNT(*) FROM file_tags ft "
                "          JOIN drive_files d ON d.drive_id = ft.drive_id "
                "         WHERE ft.tag_id = t.id AND d.trashed_at IS NULL) "
                "       AS file_count "
                "FROM tags t ORDER BY t.name COLLATE NOCASE"
            )
        )

    def rename(self, tag_id: int, name: str) -> sqlite3.Row:
        name = name.strip()
        slug = slugify(name)
        if not slug:
            raise ValueError("a tag name must contain at least one letter or digit")
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE tags SET name = ?, slug = ? WHERE id = ?",
                    (name, slug, tag_id),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateTagError(
                    f"a tag named '{slug}' already exists"
                ) from exc
            self._conn.commit()
            return self.get(tag_id)

    def recolor(self, tag_id: int, color: str) -> sqlite3.Row:
        with self._lock:
            self._conn.execute(
                "UPDATE tags SET color = ? WHERE id = ?", (color, tag_id)
            )
            self._conn.commit()
            return self.get(tag_id)

    def delete(self, tag_id: int) -> None:
        self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._conn.commit()

    def merge(self, source_id: int, target_id: int) -> int:
        """Move every assignment from source to target, then drop source."""
        if source_id == target_id:
            raise ValueError("a tag cannot be merged into itself")
        # Re-point then delete: between the two the source tag still exists
        # with its assignments already copied, so a concurrent read would
        # double-count. Held as a unit.
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO file_tags (drive_id, tag_id) "
                "SELECT drive_id, ? FROM file_tags WHERE tag_id = ?",
                (target_id, source_id),
            )
            moved = cursor.rowcount
            self._conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
            self._conn.commit()
            return moved

    # ---------- assignment ----------

    def add_files(self, tag_id: int, drive_ids: list[str]) -> int:
        added = 0
        # One tagging operation, however many batches the id list needs.
        with self._lock:
            for batch in _batched(list(dict.fromkeys(drive_ids))):
                cursor = self._conn.executemany(
                    "INSERT OR IGNORE INTO file_tags (drive_id, tag_id) "
                    "VALUES (?, ?)",
                    [(drive_id, tag_id) for drive_id in batch],
                )
                added += cursor.rowcount
            self._conn.commit()
        return added

    def remove_files(self, tag_id: int, drive_ids: list[str]) -> int:
        removed = 0
        with self._lock:
            for batch in _batched(list(dict.fromkeys(drive_ids))):
                placeholders = ",".join("?" * len(batch))
                cursor = self._conn.execute(
                    f"DELETE FROM file_tags WHERE tag_id = ? "
                    f"AND drive_id IN ({placeholders})",
                    [tag_id, *batch],
                )
                removed += cursor.rowcount
            self._conn.commit()
        return removed

    # ---------- reads for other layers ----------

    def tags_for(self, drive_ids: list[str]) -> dict[str, list[dict]]:
        """Tags per file, for the files asked about. Untagged files are absent."""
        grouped: dict[str, list[dict]] = {}
        # The cursor is iterated, not materialised, so the fetch happens after
        # `execute` has already released the lock — hold it across the loop.
        with self._lock:
            for batch in _batched(list(dict.fromkeys(drive_ids))):
                placeholders = ",".join("?" * len(batch))
                rows = self._conn.execute(
                    f"SELECT ft.drive_id, t.id, t.name, t.slug, t.color "
                    f"FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
                    f"WHERE ft.drive_id IN ({placeholders}) "
                    f"ORDER BY t.name COLLATE NOCASE",
                    batch,
                )
                for row in rows:
                    grouped.setdefault(row["drive_id"], []).append(
                        {
                            "id": row["id"], "name": row["name"],
                            "slug": row["slug"], "color": row["color"],
                        }
                    )
        return grouped

    def ensure(self, slug: str) -> sqlite3.Row:
        """The tag with this slug, created from it when absent.

        Enrich uses this to bring a `t_*` appProperty back into the catalog
        after a rebuild, so Drive is the durable copy of a tag, not just a
        mirror of one.
        """
        # Check-then-act: without the lock two callers can both miss and both
        # insert, and the loser gets a DuplicateTagError from a tag it asked
        # to have created. The lock is reentrant, so `create` may take it too.
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tags WHERE slug = ?", (slug,)
            ).fetchone()
            if row is not None:
                return row
            return self.create(slug.replace("-", " "))

    def slugs_by_file(self) -> dict[str, set[str]]:
        """Every assignment in the catalog. `sync_tags` diffs against this."""
        grouped: dict[str, set[str]] = {}
        with self._lock:
            for row in self._conn.execute(
                "SELECT ft.drive_id, t.slug FROM file_tags ft "
                "JOIN tags t ON t.id = ft.tag_id"
            ):
                grouped.setdefault(row["drive_id"], set()).add(row["slug"])
        return grouped

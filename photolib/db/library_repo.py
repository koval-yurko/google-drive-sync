"""The query behind the Library page.

Browses `drive_files` — what Drive actually holds, as of the last Scan — and
left-joins the catalog for the things only the catalog knows: capture time,
country, source archive, duplicate verdict. The join is exact rather than
name-based, because Organize records `media.drive_file_id` on every success.
A file that arrived in the destination by some other route keeps its row and
shows nulls; it is never hidden.

Month, country, type and duplicate status are computed here rather than
stored as tags. That is the whole reason Phase 4 has no `retag` action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ROW_FIELDS = (
    "drive_id", "name", "month", "mime_type", "media_type", "size", "md5",
    "capture_time", "capture_source", "country",
    "duplicate_of", "duplicate_reason", "archive_name",
)

# `media_type` in SQL, so filtering and selecting agree by construction.
_MEDIA_TYPE = (
    "CASE "
    "  WHEN d.mime_type LIKE 'image/%' THEN 'image' "
    "  WHEN d.mime_type LIKE 'video/%' THEN 'video' "
    "  ELSE 'other' "
    "END"
)

_FROM = (
    "FROM drive_files d "
    "LEFT JOIN media m ON m.drive_file_id = d.drive_id "
    "LEFT JOIN entries e ON e.id = m.entry_id "
    "LEFT JOIN archives a ON a.id = e.archive_id "
)

_SELECT = (
    "SELECT d.drive_id, d.name, d.parent_path AS month, d.mime_type, "
    f"       {_MEDIA_TYPE} AS media_type, d.size, d.md5, "
    "       m.capture_time, m.capture_source, m.country, "
    "       m.duplicate_of, m.duplicate_reason, a.name AS archive_name "
)

# Newest month first — the way you actually look for a recent photo — then by
# name within the month, which keeps a Live Photo's HEIC and MOV adjacent.
_ORDER = "ORDER BY d.parent_path DESC, d.name ASC"


@dataclass(frozen=True)
class Filters:
    month: str | None = None
    country: str | None = None
    media_type: str | None = None
    tag_id: int | None = None
    duplicates: bool = False
    search: str | None = None


def _where(filters: Filters) -> tuple[str, list]:
    clauses = ["d.trashed_at IS NULL"]
    args: list = []
    if filters.month:
        clauses.append("d.parent_path = ?")
        args.append(filters.month)
    if filters.country:
        clauses.append("m.country = ?")
        args.append(filters.country)
    if filters.media_type:
        clauses.append(f"{_MEDIA_TYPE} = ?")
        args.append(filters.media_type)
    if filters.duplicates:
        clauses.append("m.duplicate_of IS NOT NULL")
    if filters.search:
        clauses.append("d.name LIKE ? ESCAPE '\\'")
        escaped = (
            filters.search.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        args.append(f"%{escaped}%")
    if filters.tag_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM file_tags ft "
            "        WHERE ft.drive_id = d.drive_id AND ft.tag_id = ?)"
        )
        args.append(filters.tag_id)
    return "WHERE " + " AND ".join(clauses), args


class LibraryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_files(self, filters: Filters, limit: int, offset: int) -> dict:
        clause, args = _where(filters)
        total = self._conn.execute(
            f"SELECT COUNT(*) {_FROM} {clause}", args
        ).fetchone()[0]
        rows = self._conn.execute(
            f"{_SELECT} {_FROM} {clause} {_ORDER} LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        return {
            "total": total,
            "rows": [{field: row[field] for field in ROW_FIELDS} for row in rows],
        }

    def all_ids(self, filters: Filters) -> list[str]:
        """Every id matching the filter — what 'select all matching' selects."""
        clause, args = _where(filters)
        return [
            row["drive_id"]
            for row in self._conn.execute(
                f"SELECT d.drive_id {_FROM} {clause} {_ORDER}", args
            )
        ]

    def detail(self, drive_id: str) -> dict | None:
        row = self._conn.execute(
            f"{_SELECT} {_FROM} WHERE d.drive_id = ?", (drive_id,)
        ).fetchone()
        return None if row is None else {f: row[f] for f in ROW_FIELDS}

    def facets(self) -> dict:
        def group(expression: str, order: str) -> list[dict]:
            return [
                {"value": row["value"], "count": row["count"]}
                for row in self._conn.execute(
                    f"SELECT {expression} AS value, COUNT(*) AS count {_FROM} "
                    f"WHERE d.trashed_at IS NULL AND {expression} IS NOT NULL "
                    f"GROUP BY value ORDER BY {order}"
                )
            ]

        def count(clause: str) -> int:
            return self._conn.execute(
                f"SELECT COUNT(*) {_FROM} WHERE d.trashed_at IS NULL AND {clause}"
            ).fetchone()[0]

        return {
            "total": count("1 = 1"),
            "months": group("d.parent_path", "value DESC"),
            "countries": group("m.country", "count DESC, value ASC"),
            "types": group(_MEDIA_TYPE, "count DESC, value ASC"),
            "duplicates": count("m.duplicate_of IS NOT NULL"),
        }

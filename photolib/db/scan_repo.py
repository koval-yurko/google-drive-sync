"""Persistence for archive indexes and the destination folder index."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from photolib.ziparchive.reader import ZipEntry

_ENTRY_COLUMNS = (
    "archive_id, path, name, crc32, size, compressed_size, "
    "method, local_header_offset, kind"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------- archives ----------

    def upsert_archive(
        self, drive_id: str, name: str, size: int, modified_time: str | None
    ) -> int:
        self._conn.execute(
            "INSERT INTO archives (drive_id, name, size, modified_time) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(drive_id) DO UPDATE SET "
            "  name = excluded.name, size = excluded.size, "
            "  modified_time = excluded.modified_time",
            (drive_id, name, size, modified_time),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM archives WHERE drive_id = ?", (drive_id,)
        ).fetchone()
        return row["id"]

    def archive_is_current(
        self, drive_id: str, size: int, modified_time: str | None
    ) -> bool:
        """True when this archive was indexed and has not changed since."""
        row = self._conn.execute(
            "SELECT size, modified_time, indexed_at FROM archives WHERE drive_id = ?",
            (drive_id,),
        ).fetchone()
        if row is None or row["indexed_at"] is None:
            return False
        return row["size"] == size and row["modified_time"] == modified_time

    def mark_indexed(self, archive_id: int) -> None:
        self._conn.execute(
            "UPDATE archives SET indexed_at = ? WHERE id = ?", (_now(), archive_id)
        )
        self._conn.commit()

    # ---------- entries ----------

    def replace_entries(
        self, archive_id: int, entries: list[ZipEntry], kinds: dict[str, str]
    ) -> None:
        self._conn.execute("DELETE FROM entries WHERE archive_id = ?", (archive_id,))
        self._conn.executemany(
            f"INSERT INTO entries ({_ENTRY_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    archive_id, e.path, e.name, e.crc32, e.size,
                    e.compressed_size, e.method, e.local_header_offset,
                    kinds[e.path],
                )
                for e in entries
            ],
        )
        self._conn.commit()

    def entries_of_kind(self, kind: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT e.*, a.drive_id AS archive_drive_id, a.name AS archive_name, "
                "       a.size AS archive_size "
                "FROM entries e JOIN archives a ON a.id = e.archive_id "
                "WHERE e.kind = ? ORDER BY a.name, e.path",
                (kind,),
            )
        )

    # ---------- destination index ----------

    def upsert_drive_files(self, rows: list[dict]) -> None:
        """Refresh the destination index without disturbing tags.

        Tags key on `drive_id`, so deleting and re-inserting this table — as
        an earlier version did — silently threw every tag away. Upserting
        keeps the row; the sweep afterwards drops only what Drive no longer
        has. The sweep compares `indexed_at` rather than listing ids, because
        1,284 ids exceed SQLite's host-parameter limit.
        """
        stamp = _now()
        self._conn.executemany(
            "INSERT INTO drive_files "
            "  (drive_id, name, parent_path, md5, size, mime_type, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(drive_id) DO UPDATE SET "
            "  name = excluded.name, parent_path = excluded.parent_path, "
            "  md5 = excluded.md5, size = excluded.size, "
            "  mime_type = excluded.mime_type, indexed_at = excluded.indexed_at, "
            "  trashed_at = NULL",
            [
                (
                    r["drive_id"], r["name"], r["parent_path"], r["md5"],
                    r["size"], r.get("mime_type"), stamp,
                )
                for r in rows
            ],
        )
        self._conn.execute(
            "DELETE FROM drive_files WHERE indexed_at IS NOT ?", (stamp,)
        )
        self._conn.commit()

    def drive_file_names(self) -> dict[str, list[sqlite3.Row]]:
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in self._conn.execute("SELECT * FROM drive_files"):
            grouped[row["name"]].append(row)
        return grouped

    # ---------- reporting ----------

    def counts(self) -> dict[str, int]:
        def one(sql: str, *args) -> int:
            return self._conn.execute(sql, args).fetchone()[0]

        return {
            "archives": one("SELECT COUNT(*) FROM archives"),
            "entries": one("SELECT COUNT(*) FROM entries"),
            "media": one("SELECT COUNT(*) FROM entries WHERE kind = 'media'"),
            "sidecars": one("SELECT COUNT(*) FROM entries WHERE kind = 'sidecar'"),
            "drive_files": one("SELECT COUNT(*) FROM drive_files"),
        }

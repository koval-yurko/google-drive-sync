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
        self._lock = conn.lock

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
            "  (drive_id, name, parent_path, md5, size, mime_type, capture_hint, "
            "   indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(drive_id) DO UPDATE SET "
            "  name = excluded.name, parent_path = excluded.parent_path, "
            "  md5 = excluded.md5, size = excluded.size, "
            "  mime_type = excluded.mime_type, capture_hint = excluded.capture_hint, "
            "  indexed_at = excluded.indexed_at, "
            "  trashed_at = NULL",
            [
                (
                    r["drive_id"], r["name"], r["parent_path"], r["md5"],
                    r["size"], r.get("mime_type"), r.get("capture_hint"), stamp,
                )
                for r in rows
            ],
        )
        self._conn.execute(
            "DELETE FROM drive_files WHERE indexed_at IS NOT ?", (stamp,)
        )
        self._conn.commit()

    def record_drive_file(
        self,
        *,
        drive_id: str,
        name: str,
        parent_path: str,
        md5: str,
        size: int,
        mime_type: str,
        capture_hint: int | None = None,
    ) -> None:
        """One verified upload, straight from Organize.

        The same upsert the scan uses, minus the sweep — one new file says
        nothing about whether the rest of the index is still true.
        """
        self._conn.execute(
            "INSERT INTO drive_files "
            "  (drive_id, name, parent_path, md5, size, mime_type, capture_hint, "
            "   indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(drive_id) DO UPDATE SET "
            "  name = excluded.name, parent_path = excluded.parent_path, "
            "  md5 = excluded.md5, size = excluded.size, "
            "  mime_type = excluded.mime_type, capture_hint = excluded.capture_hint, "
            "  indexed_at = excluded.indexed_at, "
            "  trashed_at = NULL",
            (drive_id, name, parent_path, md5, size, mime_type, capture_hint, _now()),
        )
        self._conn.commit()

    def drive_file_names(self) -> dict[str, list[sqlite3.Row]]:
        """Live files only: a copy sitting in Drive's trash duplicates nothing."""
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in self._conn.execute(
            "SELECT * FROM drive_files WHERE trashed_at IS NULL"
        ):
            grouped[row["name"]].append(row)
        return grouped

    def live_drive_ids(self) -> set[str]:
        """Drive ids the last scan saw untrashed."""
        with self._lock:
            return {
                row["drive_id"]
                for row in self._conn.execute(
                    "SELECT drive_id FROM drive_files WHERE trashed_at IS NULL"
                )
            }

    def set_enrichment(
        self,
        drive_id: str,
        *,
        capture_hint: int | None,
        latitude: float | None,
        longitude: float | None,
        country: str | None,
        metadata_source: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE drive_files SET capture_hint = COALESCE(?, capture_hint), "
                "latitude = ?, longitude = ?, country = ?, metadata_source = ? "
                "WHERE drive_id = ?",
                (capture_hint, latitude, longitude, country,
                 metadata_source, drive_id),
            )
            self._conn.commit()

    def unenriched(self) -> list[sqlite3.Row]:
        """Live files Enrich has never looked at."""
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT * FROM drive_files "
                    "WHERE trashed_at IS NULL AND metadata_source IS NULL"
                )
            )

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

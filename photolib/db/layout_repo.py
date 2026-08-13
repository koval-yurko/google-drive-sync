"""Cross-table reads and writes for the library's folder layout.

`drive_files` belongs to ScanRepo and `media` to MediaRepo, but the layout
questions — how many files does each month hold, where does each one sit
now — span both, so they belong to neither. This repo is named for the
module that asks them, `photolib.planning.layout`.

Months are computed in SQL rather than in Python because the alternative
would be importing `planning.buckets.month_of` into the persistence layer.
`strftime('%Y-%m', t, 'unixepoch')` is exactly what `month_of` does.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

_UNACCOUNTED = """
    SELECT d.drive_id, strftime('%Y-%m', d.capture_hint, 'unixepoch') AS month
    FROM drive_files d
    LEFT JOIN media m ON m.drive_file_id = d.drive_id
    WHERE d.trashed_at IS NULL AND m.id IS NULL AND d.capture_hint IS NOT NULL
"""

_CATALOGUED = """
    SELECT drive_file_id, strftime('%Y-%m', capture_time, 'unixepoch') AS month
    FROM media
    WHERE capture_time IS NOT NULL
"""

_LIVE_FILES = """
    SELECT d.drive_id, d.name, d.parent_path, d.md5, m.id AS media_id,
           CASE WHEN m.id IS NULL THEN d.capture_hint
                ELSE m.capture_time END AS capture
    FROM drive_files d
    LEFT JOIN media m ON m.drive_file_id = d.drive_id
    WHERE d.trashed_at IS NULL
    ORDER BY d.parent_path, d.name
"""


class LayoutRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def unaccounted_months(
        self, exclude: set[str] = frozenset()
    ) -> Counter[str]:
        """Live Drive files no media row accounts for, by capture hint.

        These are the legacy files that predate the pipeline; the catalog
        knows nothing about them beyond what Drive itself reports.
        """
        with self._lock:
            rows = list(self._conn.execute(_UNACCOUNTED))
        return Counter(
            row["month"] for row in rows if row["drive_id"] not in exclude
        )

    def capture_histogram(
        self, exclude: set[str] = frozenset()
    ) -> Counter[str]:
        """Every file the library will hold, by month: catalogued media
        (uploaded or not) plus the unaccounted Drive files.

        `exclude` drops drive ids from both halves, so a file that is about
        to be trashed does not reserve space in the bucket its month would
        otherwise need.
        """
        # Both halves must describe one state of the catalog.
        with self._lock:
            unaccounted = list(self._conn.execute(_UNACCOUNTED))
            catalogued = list(self._conn.execute(_CATALOGUED))
        counts = Counter(
            row["month"] for row in unaccounted if row["drive_id"] not in exclude
        )
        counts.update(
            row["month"]
            for row in catalogued
            if row["drive_file_id"] not in exclude
        )
        return counts

    def live_files_for_layout(
        self, exclude: set[str] = frozenset()
    ) -> list[sqlite3.Row]:
        """Every live file with the capture time that dates it: the media
        row's where there is one, the Drive hint otherwise."""
        with self._lock:
            return [
                row
                for row in self._conn.execute(_LIVE_FILES)
                if row["drive_id"] not in exclude
            ]

    def record_move(self, drive_id: str, folder: str, name: str) -> None:
        """Record that a file now sits in `folder` under `name`.

        One transaction: a crash between the two statements would otherwise
        leave `drive_files` and `media` disagreeing about where the file is.
        The connection is in autocommit mode, so the BEGIN is explicit.
        """
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "UPDATE drive_files SET parent_path = ?, name = ? "
                    "WHERE drive_id = ?",
                    (folder, name, drive_id),
                )
                self._conn.execute(
                    "UPDATE media SET target_folder = ?, target_name = ? "
                    "WHERE drive_file_id = ?",
                    (folder, name, drive_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

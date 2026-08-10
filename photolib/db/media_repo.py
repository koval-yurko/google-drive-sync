"""Persistence for sidecars and per-media planning results."""

from __future__ import annotations

import sqlite3

_SIDECAR_FIELDS = (
    "title", "photo_taken_time", "creation_time",
    "latitude", "longitude", "altitude", "url", "device",
)

_PLAN_FIELDS = (
    "capture_time", "capture_source", "latitude", "longitude",
    "place", "country", "target_folder", "target_name",
    "duplicate_of", "duplicate_reason",
)

_MEDIA_SELECT = """
    SELECT m.*, e.path, e.name, e.size AS entry_size, e.crc32,
           a.name AS archive_name, a.drive_id AS archive_drive_id
    FROM media m
    JOIN entries e ON e.id = m.entry_id
    JOIN archives a ON a.id = e.archive_id
"""


class MediaRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------- sidecars ----------

    def save_sidecar(self, entry_id: int, parsed: dict, raw_json: str) -> int:
        columns = ", ".join(_SIDECAR_FIELDS)
        placeholders = ", ".join("?" for _ in _SIDECAR_FIELDS)
        updates = ", ".join(f"{f} = excluded.{f}" for f in _SIDECAR_FIELDS)
        values = [parsed.get(f) for f in _SIDECAR_FIELDS]
        self._conn.execute(
            f"INSERT INTO sidecars (entry_id, {columns}, raw_json) "
            f"VALUES (?, {placeholders}, ?) "
            f"ON CONFLICT(entry_id) DO UPDATE SET {updates}, raw_json = excluded.raw_json",
            [entry_id, *values, raw_json],
        )
        self._conn.commit()
        return self._conn.execute(
            "SELECT id FROM sidecars WHERE entry_id = ?", (entry_id,)
        ).fetchone()["id"]

    def unpaired_sidecars(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT e.* FROM entries e "
                "WHERE e.kind = 'sidecar' AND e.id NOT IN ("
                "  SELECT s.entry_id FROM sidecars s "
                "  JOIN media m ON m.sidecar_id = s.id"
                ") ORDER BY e.name"
            )
        )

    # ---------- media ----------

    def upsert_media(self, entry_id: int, **fields) -> int:
        self._conn.execute(
            "INSERT INTO media (entry_id) VALUES (?) "
            "ON CONFLICT(entry_id) DO NOTHING",
            (entry_id,),
        )
        self._conn.commit()
        media_id = self._conn.execute(
            "SELECT id FROM media WHERE entry_id = ?", (entry_id,)
        ).fetchone()["id"]
        if fields:
            self.set_plan(entry_id, **fields)
        return media_id

    def link_sidecar(self, entry_id: int, sidecar_id: int) -> None:
        self._conn.execute(
            "UPDATE media SET sidecar_id = ? WHERE entry_id = ?", (sidecar_id, entry_id)
        )
        self._conn.commit()

    def set_plan(self, entry_id: int, **fields) -> None:
        unknown = set(fields) - set(_PLAN_FIELDS)
        if unknown:
            raise ValueError(f"unknown planning field(s): {sorted(unknown)}")
        assignments = ", ".join(f"{f} = ?" for f in fields)
        self._conn.execute(
            f"UPDATE media SET {assignments} WHERE entry_id = ?",
            [*fields.values(), entry_id],
        )
        self._conn.commit()

    def clear_plan(self) -> None:
        """Reset planning columns so Plan can be re-run; upload results survive."""
        assignments = ", ".join(f"{f} = NULL" for f in _PLAN_FIELDS)
        self._conn.execute(f"UPDATE media SET {assignments}")
        self._conn.commit()

    def all_media(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(f"{_MEDIA_SELECT} ORDER BY a.name, e.path")
        )

    def summary(self) -> dict:
        def one(sql: str) -> int:
            return self._conn.execute(sql).fetchone()[0]

        media = one("SELECT COUNT(*) FROM media")
        planned = one("SELECT COUNT(*) FROM media WHERE target_folder IS NOT NULL")
        return {
            "media": media,
            "planned": planned,
            "unplanned": media - planned,
            "duplicates": one("SELECT COUNT(*) FROM media WHERE duplicate_of IS NOT NULL"),
            "with_place": one("SELECT COUNT(*) FROM media WHERE place IS NOT NULL"),
            "with_sidecar": one("SELECT COUNT(*) FROM media WHERE sidecar_id IS NOT NULL"),
            "pending": one("SELECT COUNT(*) FROM media WHERE upload_status = 'pending'"),
        }

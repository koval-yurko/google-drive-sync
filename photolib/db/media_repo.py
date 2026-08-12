"""Persistence for sidecars and per-media planning results."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SIDECAR_FIELDS = (
    "title", "photo_taken_time", "creation_time",
    "latitude", "longitude", "altitude", "url", "device",
)

_PLAN_FIELDS = (
    "capture_time", "capture_source", "latitude", "longitude",
    "country", "target_folder", "target_name",
    "duplicate_of", "duplicate_reason",
    "plan_verdict", "plan_match",
)

# For a `done` row, target_folder/target_name are no longer a *plan* — they
# are the record of where the file actually is (mark_uploaded sets them to
# match reality on adoption; an ordinary upload's Plan-computed value is
# simply correct going forward). Plan's job is choosing destinations for
# files that have not moved yet, so both `set_plan` and `clear_plan` must
# leave these two columns alone once upload_status is 'done', however many
# times Plan is re-run. Every other planning column still resets/recomputes
# normally — a `done` row's capture time, country, and verdict are still
# fair game.
_DONE_PROTECTS = ("target_folder", "target_name")

PLAN_VERDICTS = ("skip", "verify", "upload")

_MEDIA_SELECT = """
    SELECT m.*, e.path, e.name, e.size AS entry_size, e.crc32,
           a.name AS archive_name, a.drive_id AS archive_drive_id
    FROM media m
    JOIN entries e ON e.id = m.entry_id
    JOIN archives a ON a.id = e.archive_id
"""

_UPLOAD_SELECT = """
    SELECT m.id AS media_id, m.entry_id, m.target_folder, m.target_name,
           m.capture_time, m.country, m.upload_status,
           m.upload_session_uri, m.upload_offset, m.session_started_at,
           m.attempts, m.plan_verdict, m.plan_match,
           m.drive_file_id, m.md5,
           e.path, e.name, e.crc32, e.size, e.compressed_size,
           e.method, e.local_header_offset,
           a.drive_id AS archive_drive_id, a.name AS archive_name,
           df.md5 AS match_md5, df.parent_path AS match_parent_path,
           df.name AS match_name
    FROM media m
    JOIN entries e ON e.id = m.entry_id
    JOIN archives a ON a.id = e.archive_id
    LEFT JOIN drive_files df ON df.drive_id = m.plan_match
                            AND df.trashed_at IS NULL
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
        verdict = fields.get("plan_verdict")
        if verdict is not None and verdict not in PLAN_VERDICTS:
            raise ValueError(f"unknown plan verdict: {verdict!r}")
        # See `_DONE_PROTECTS`: a `done` row keeps its current
        # target_folder/target_name no matter what the caller passes in,
        # expressed as a CASE against the row's own upload_status so this is
        # one atomic UPDATE rather than a read-then-write.
        assignments = []
        params: list = []
        for field, value in fields.items():
            if field in _DONE_PROTECTS:
                assignments.append(
                    f"{field} = CASE WHEN upload_status = 'done' "
                    f"THEN {field} ELSE ? END"
                )
            else:
                assignments.append(f"{field} = ?")
            params.append(value)
        params.append(entry_id)
        self._conn.execute(
            f"UPDATE media SET {', '.join(assignments)} WHERE entry_id = ?",
            params,
        )
        self._conn.commit()

    def clear_plan(self) -> None:
        """Reset planning columns so Plan can be re-run; upload results survive.

        `target_folder`/`target_name` are additionally left untouched for
        `done` rows — see `_DONE_PROTECTS` — so a Plan re-run can never
        erase (even momentarily) the location an upload already confirmed.
        """
        resettable = [f for f in _PLAN_FIELDS if f not in _DONE_PROTECTS]
        assignments = ", ".join(f"{f} = NULL" for f in resettable)
        protected = ", ".join(
            f"{f} = CASE WHEN upload_status = 'done' THEN {f} ELSE NULL END"
            for f in _DONE_PROTECTS
        )
        self._conn.execute(f"UPDATE media SET {assignments}, {protected}")
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
            "with_sidecar": one("SELECT COUNT(*) FROM media WHERE sidecar_id IS NOT NULL"),
            "pending": one(
                "SELECT COUNT(*) FROM media WHERE upload_status = 'pending' "
                "AND COALESCE(plan_verdict, 'upload') != 'skip'"
            ),
            "skipped": one(
                "SELECT COUNT(*) FROM media WHERE plan_verdict = 'skip'"
            ),
            "uploaded": one("SELECT COUNT(*) FROM media WHERE upload_status = 'done'"),
            "errors": one("SELECT COUNT(*) FROM media WHERE upload_status = 'error'"),
        }

    # ---------- uploads ----------

    def pending_uploads(
        self, retry_errors: bool = False, limit: int | None = None
    ) -> list[sqlite3.Row]:
        """Work for the Organize action, in an order that keeps reads local."""
        statuses = ("pending", "error") if retry_errors else ("pending",)
        placeholders = ", ".join("?" for _ in statuses)
        sql = (
            f"{_UPLOAD_SELECT} WHERE m.upload_status IN ({placeholders}) "
            "AND m.target_folder IS NOT NULL "
            "AND COALESCE(m.plan_verdict, 'upload') != 'skip' "
            "ORDER BY a.name, e.local_header_offset"
        )
        args: list = list(statuses)
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return list(self._conn.execute(sql, args))

    def save_session(self, entry_id: int, session_uri: str) -> None:
        """Record a session as soon as it exists, so a crash can resume it."""
        self._conn.execute(
            "UPDATE media SET upload_session_uri = ?, session_started_at = ? "
            "WHERE entry_id = ?",
            (session_uri, _now(), entry_id),
        )
        self._conn.commit()

    def mark_uploaded(
        self,
        entry_id: int,
        drive_file_id: str,
        md5: str,
        target_folder: str | None = None,
        target_name: str | None = None,
    ) -> None:
        """Record a finished transfer.

        `target_folder`/`target_name` are for the adopt path only: an adopted
        file is left wherever it already lived in Drive, not moved to the
        bucket Plan computed, so the catalog must be corrected to match reality
        in the same write — otherwise Verify Library reports the app's own
        adoption as drift. A plain upload lands exactly where Plan said, so
        those columns are left untouched for it.
        """
        assignments = [
            "upload_status = 'done'", "drive_file_id = ?", "md5 = ?",
            "error = NULL", "upload_session_uri = NULL",
            "session_started_at = NULL", "upload_offset = 0",
        ]
        params: list = [drive_file_id, md5]
        if target_folder is not None:
            assignments.append("target_folder = ?")
            params.append(target_folder)
        if target_name is not None:
            assignments.append("target_name = ?")
            params.append(target_name)
        params.append(entry_id)
        self._conn.execute(
            f"UPDATE media SET {', '.join(assignments)} WHERE entry_id = ?",
            params,
        )
        self._conn.commit()

    def mark_failed(self, entry_id: int, reason: str, offset: int = 0) -> None:
        """Record a failure, keeping the session so a retry can resume it."""
        self._conn.execute(
            "UPDATE media SET upload_status = 'error', error = ?, "
            "upload_offset = ?, attempts = attempts + 1 WHERE entry_id = ?",
            (reason, offset, entry_id),
        )
        self._conn.commit()

    def reset_upload(self, entry_id: int) -> None:
        """Forget everything about a previous attempt and queue it afresh."""
        self._conn.execute(
            "UPDATE media SET upload_status = 'pending', error = NULL, "
            "upload_session_uri = NULL, session_started_at = NULL, "
            "upload_offset = 0 WHERE entry_id = ?",
            (entry_id,),
        )
        self._conn.commit()

    def uploaded_by_name(self) -> dict[str, sqlite3.Row]:
        """Verified uploads keyed by original filename.

        Only rows Drive confirmed: `done`, with a file id and an MD5. This is
        the evidence Clear Stale Trees gates on.
        """
        rows = self._conn.execute(
            f"{_UPLOAD_SELECT} WHERE m.upload_status = 'done' "
            "AND m.drive_file_id IS NOT NULL AND m.md5 IS NOT NULL"
        )
        return {row["name"]: row for row in rows}

    def verified_by_crc(self) -> dict[tuple[int, int], sqlite3.Row]:
        """Verified uploads keyed by (crc32, uncompressed size).

        The bridge between a ZIP entry, which only knows CRC32, and Drive,
        which only knows MD5: these are bytes this app itself uploaded and
        Drive confirmed, so their identity is settled without a download.
        """
        rows = self._conn.execute(
            f"{_UPLOAD_SELECT} WHERE m.upload_status = 'done' "
            "AND m.drive_file_id IS NOT NULL AND m.md5 IS NOT NULL"
        )
        return {(row["crc32"], row["size"]): row for row in rows}

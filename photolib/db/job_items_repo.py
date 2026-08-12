"""Per-item checkpoints for long-running actions.

One row per unit of work, keyed by (run_id, phase, item_key). A phase
enumerates its work here and processes only what `pending()` returns, so a
resumed run never repeats finished work. The same table doubles as a
persisted dry-run plan: `detail` holds the JSON of an intended operation,
and confirming reads it back instead of re-planning.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

STATES = ("pending", "done", "failed", "skipped")

# 'skipped' is a decision, not an interruption: it is never retried.
_RETRYABLE = ("pending", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["detail"] = json.loads(data["detail"]) if data["detail"] else None
    return data


class JobItemsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # Shared with every other repo over this connection; see
        # catalog.LockedConnection.
        self._lock = conn.lock

    def enumerate(
        self, run_id: str, phase: str, keys: list[str], job_id: str
    ) -> None:
        """Declare this phase's work. Rows that already exist are untouched."""
        with self._lock:
            self._conn.executemany(
                "INSERT INTO job_items "
                "(run_id, phase, item_key, job_id, state, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?) "
                "ON CONFLICT (run_id, phase, item_key) DO NOTHING",
                [(run_id, phase, key, job_id, _now()) for key in keys],
            )
            self._conn.commit()

    def put(
        self,
        run_id: str,
        phase: str,
        item_key: str,
        job_id: str,
        state: str,
        detail: dict | None = None,
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown job item state: {state!r}")
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_items "
                "(run_id, phase, item_key, job_id, state, detail, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id, phase, item_key) DO UPDATE SET "
                "job_id = excluded.job_id, state = excluded.state, "
                "detail = COALESCE(excluded.detail, job_items.detail), "
                "updated_at = excluded.updated_at",
                (
                    run_id, phase, item_key, job_id, state,
                    json.dumps(detail) if detail is not None else None,
                    _now(),
                ),
            )
            self._conn.commit()

    def mark(
        self,
        run_id: str,
        phase: str,
        item_key: str,
        state: str,
        detail: dict | None = None,
    ) -> None:
        """Update an item's state, folding `detail` into whatever plan is
        already stored rather than replacing it.

        The persisted `detail` is a dry run's plan — e.g. a `dedupe.Removal`
        or `repack.Move` — and confirming reads it straight back to execute
        it. A caller marking an item `failed` passes a small dict like
        `{"error": str(exc)}`; merging it on top of the existing plan keeps
        both the error *and* the plan a resume needs, instead of the error
        silently destroying the plan (see photolib/actions/reorganize_library.py,
        which strips the "error" key back out before reconstructing the
        dataclass on resume).
        """
        if state not in STATES:
            raise ValueError(f"unknown job item state: {state!r}")
        with self._lock:
            if detail is not None:
                row = self._conn.execute(
                    "SELECT detail FROM job_items "
                    "WHERE run_id = ? AND phase = ? AND item_key = ?",
                    (run_id, phase, item_key),
                ).fetchone()
                existing = (
                    json.loads(row["detail"])
                    if row is not None and row["detail"]
                    else None
                )
                if existing is not None:
                    detail = {**existing, **detail}
            self._conn.execute(
                "UPDATE job_items SET state = ?, "
                "detail = COALESCE(?, detail), updated_at = ? "
                "WHERE run_id = ? AND phase = ? AND item_key = ?",
                (
                    state,
                    json.dumps(detail) if detail is not None else None,
                    _now(), run_id, phase, item_key,
                ),
            )
            self._conn.commit()

    def pending(self, run_id: str, phase: str) -> list[dict]:
        placeholders = ", ".join("?" for _ in _RETRYABLE)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM job_items WHERE run_id = ? AND phase = ? "
                f"AND state IN ({placeholders}) ORDER BY id",
                (run_id, phase, *_RETRYABLE),
            ).fetchall()
        return [_decode(r) for r in rows]

    def all(
        self, run_id: str, phase: str | None = None, state: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM job_items WHERE run_id = ?"
        args: list = [run_id]
        if phase is not None:
            sql += " AND phase = ?"
            args.append(phase)
        if state is not None:
            sql += " AND state = ?"
            args.append(state)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY id", args).fetchall()
        return [_decode(r) for r in rows]

    def counts(self, run_id: str, phase: str | None = None) -> dict[str, int]:
        sql = "SELECT state, COUNT(*) AS n FROM job_items WHERE run_id = ?"
        args: list = [run_id]
        if phase is not None:
            sql += " AND phase = ?"
            args.append(phase)
        with self._lock:
            rows = self._conn.execute(sql + " GROUP BY state", args).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def clear(self, run_id: str, phase: str | None = None) -> None:
        sql = "DELETE FROM job_items WHERE run_id = ?"
        args: list = [run_id]
        if phase is not None:
            sql += " AND phase = ?"
            args.append(phase)
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

"""Persistence for background jobs and their log events."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(BaseModel):
    id: str
    action: str
    params: dict
    status: str
    progress: float
    message: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    run_id: str | None = None
    resumed_from: str | None = None
    phase: str | None = None
    items_done: int = 0
    items_total: int = 0


class JobEvent(BaseModel):
    id: int
    job_id: str
    ts: float
    level: str
    message: str


def _to_job(row: sqlite3.Row) -> Job:
    data = dict(row)
    data["params"] = json.loads(data["params"])
    return Job.model_validate(data)


class JobsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        # Shared with every other repo over this connection (see
        # catalog.LockedConnection): a per-instance lock cannot provide
        # mutual exclusion for a connection used by multiple repo classes.
        # Reentrant because create() calls self.get() while already holding
        # it, and get()/list()/events() must also hold it so that reads
        # never execute concurrently with a write on the shared connection.
        self._lock = conn.lock

    def create(
        self,
        action: str,
        params: dict,
        run_id: str | None = None,
        resumed_from: str | None = None,
    ) -> Job:
        with self._lock:
            job_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO jobs "
                "(id, action, params, status, progress, created_at, "
                " run_id, resumed_from) "
                "VALUES (?, ?, ?, 'queued', 0.0, ?, ?, ?)",
                (
                    job_id, action, json.dumps(params), _now(),
                    run_id or uuid.uuid4().hex, resumed_from,
                ),
            )
            self._conn.commit()
            job = self.get(job_id)
            assert job is not None
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return _to_job(row) if row else None

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_to_job(r) for r in rows]

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (_now(), job_id),
            )
            self._conn.commit()

    def mark_done(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'done', progress = 1.0, finished_at = ? "
                "WHERE id = ?",
                (_now(), job_id),
            )
            self._conn.commit()

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
                "WHERE id = ?",
                (error, _now(), job_id),
            )
            self._conn.commit()

    def mark_cancelled(self, job_id: str) -> bool:
        """Mark a job cancelled, but only while it is still queued or
        running. The status guard makes this atomic against a concurrent
        terminal transition: without it, a caller that read a stale
        'queued'/'running' snapshot could overwrite an already-done or
        already-failed row back to 'cancelled' after the fact. Returns
        whether a row was actually changed, so callers can tell a real
        cancellation from a no-op on a job that finished first.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? "
                "WHERE id = ? AND status IN ('queued', 'running')",
                (_now(), job_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def update_progress(
        self,
        job_id: str,
        progress: float,
        message: str | None = None,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET progress = ?, "
                "message = COALESCE(?, message), phase = COALESCE(?, phase), "
                "items_done = COALESCE(?, items_done), "
                "items_total = COALESCE(?, items_total) "
                "WHERE id = ?",
                (progress, message, phase, done, total, job_id),
            )
            self._conn.commit()

    def add_event(self, job_id: str, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_events (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
                (job_id, time.time(), level, message),
            )
            self._conn.commit()

    def events(self, job_id: str, after_id: int = 0) -> list[JobEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after_id),
            ).fetchall()
            return [JobEvent.model_validate(dict(r)) for r in rows]

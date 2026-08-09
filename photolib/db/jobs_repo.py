"""Persistence for background jobs and their log events."""

from __future__ import annotations

import json
import sqlite3
import threading
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
        self._lock = threading.Lock()

    def create(self, action: str, params: dict) -> Job:
        with self._lock:
            job_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO jobs (id, action, params, status, progress, created_at) "
                "VALUES (?, ?, ?, 'queued', 0.0, ?)",
                (job_id, action, json.dumps(params), _now()),
            )
            self._conn.commit()
            job = self.get(job_id)
            assert job is not None
            return job

    def get(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _to_job(row) if row else None

    def list(self, limit: int = 50) -> list[Job]:
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

    def update_progress(
        self, job_id: str, progress: float, message: str | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET progress = ?, message = COALESCE(?, message) "
                "WHERE id = ?",
                (progress, message, job_id),
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
        rows = self._conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
            (job_id, after_id),
        ).fetchall()
        return [JobEvent.model_validate(dict(r)) for r in rows]

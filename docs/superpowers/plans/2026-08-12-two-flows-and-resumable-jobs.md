# Two Flows and Resumable Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the eleven-action pipeline into two operator-facing flows — Sync from Archives and Reorganize Folders — backed by per-item checkpoints that make every phase resumable and cancellable.

**Architecture:** Nothing is deleted. The existing action modules stay registered under an Advanced group and the two new flow modules drive their `run()` generators through a `run_phase` helper that rescales progress into a span. A new `job_items` table serves double duty as the checkpoint ledger and as the persisted dry-run plan, so "resume skips finished work" and "confirm executes exactly what you read" are the same mechanism.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, SQLite (`sqlite3` stdlib), pytest; React 18 + Vite + TypeScript + vitest.

## Global Constraints

- Python 3.12, managed by `uv`. Run backend tests with `uv run pytest`.
- The default backend suite **never touches the network.** Drive is faked by `tests/fakes/fake_drive.py`; ZIPs are built in memory by `tests/fixtures/zipbuilder.py`. Tests needing real Drive are marked `@pytest.mark.live` and are not part of this plan.
- Every SQLite write goes through a repo class that holds `conn.lock` (see `photolib/db/catalog.py`). One shared reentrant lock per connection — never create a new lock in a repo.
- Action modules must declare `ID`, `TITLE`, `DESCRIPTION`, `ORDER`, `Params`, `run`. `run` must be a generator function or the registry rejects it.
- `ActionParams` sets `extra="forbid"`. Unknown params are a 422, not a shrug.
- SQLite cannot alter a CHECK constraint. New columns are added by `ALTER TABLE ADD COLUMN` in `migrations._ADDED_COLUMNS` **and** written into `schema.sql`, and the two paths must produce identical tables — `tests/test_migrations.py` asserts it.
- Destructive Drive operations **trash**, never delete.
- Frontend tests: `cd web && npm test`.
- Commit after every task.

## Deviations from the spec (accepted while planning)

Three details in the spec did not survive contact with the Drive API or with a
concrete design. They are implemented as described here:

1. **No `videoMediaMetadata`, no `cameraMake`/`cameraModel`.** Drive's
   `videoMediaMetadata` exposes only width, height and duration — there is no
   video capture time to read, so videos keep today's `createdTime` fallback.
   Camera fields have no consumer now that there is no rule engine. `FILE_FIELDS`
   gains `imageMediaMetadata(time,location)` and `appProperties` only.
2. **`media` gains two columns, not one:** `plan_verdict` and `plan_match`. The
   verdict needs to name the Drive file it matched against, and `duplicate_of`
   already means something else (a parent path, for display).
3. **Index-phase checkpoints record each folder's child folder ids** in
   `job_items.detail`. Skipping a folder on resume would otherwise lose its
   subfolders, because the walk discovers children by listing.

## File Structure

**New backend files**

| File | Responsibility |
| --- | --- |
| `photolib/db/job_items_repo.py` | The checkpoint ledger: enumerate, mark, query |
| `photolib/actions/phases.py` | `run_phase` — rescale a sub-action's progress into a span |
| `photolib/scan.py` | `index_destination` — the destination walk, shared by Scan and Reorganize |
| `photolib/enrich.py` | `enrichment_for` — date, coordinates, country and tag slugs from a `DriveFile` |
| `photolib/dedupe.py` | `plan_removals` / `apply_removal` — duplicate planning, split from the action |
| `photolib/repack.py` | `plan_moves` / `apply_move` / `plan_sweep` — bucket moves, split from the action |
| `photolib/actions/sync_archives.py` | Flow 1 |
| `photolib/actions/reorganize_library.py` | Flow 2 |
| `photolib/actions/verify_library.py` | Read-only drift report |

**Modified backend files**

`photolib/db/schema.sql`, `photolib/db/migrations.py`, `photolib/db/jobs_repo.py`, `photolib/db/media_repo.py`, `photolib/db/scan_repo.py`, `photolib/db/tags_repo.py`, `photolib/db/library_repo.py`, `photolib/actions/base.py`, `photolib/actions/registry.py`, `photolib/actions/scan_archives.py`, `photolib/actions/plan_organize.py`, `photolib/actions/organize.py`, `photolib/actions/reorganize.py`, `photolib/actions/clear_duplicates.py`, `photolib/jobs/runner.py`, `photolib/drive/client.py`, `photolib/transfer.py`, `photolib/api/routes_jobs.py`, `photolib/api/routes_actions.py`, `README.md`.

**Modified frontend files**

`web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/components/Nav.tsx`, `web/src/components/JobProgress.tsx`, `web/src/pages/JobsPage.tsx`, `web/src/pages/ReviewPage.tsx`.

---

### Task 1: The checkpoint ledger

**Files:**
- Modify: `photolib/db/schema.sql`
- Modify: `photolib/db/migrations.py:17` (`SCHEMA_VERSION`)
- Create: `photolib/db/job_items_repo.py`
- Test: `tests/test_job_items_repo.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect` (test fixture `conn`).
- Produces:
  - `JobItemsRepo(conn)` with
    `enumerate(run_id: str, phase: str, keys: list[str], job_id: str) -> None`,
    `put(run_id: str, phase: str, item_key: str, job_id: str, state: str, detail: dict | None = None) -> None`,
    `mark(run_id: str, phase: str, item_key: str, state: str, detail: dict | None = None) -> None`,
    `pending(run_id: str, phase: str) -> list[dict]`,
    `all(run_id: str, phase: str | None = None, state: str | None = None) -> list[dict]`,
    `counts(run_id: str, phase: str | None = None) -> dict[str, int]`,
    `clear(run_id: str, phase: str | None = None) -> None`.
  - Rows expose `run_id, phase, item_key, job_id, state, detail, updated_at`.
    `detail` is decoded from JSON into a `dict` by `all`/`pending`, or `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_job_items_repo.py
import pytest

from photolib.db.job_items_repo import JobItemsRepo


def test_enumerate_creates_pending_rows(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    assert [r["item_key"] for r in repo.pending("run1", "upload")] == ["a", "b"]
    assert all(r["state"] == "pending" for r in repo.pending("run1", "upload"))


def test_enumerate_does_not_reset_finished_items(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    repo.mark("run1", "upload", "a", "done")
    repo.enumerate("run1", "upload", ["a", "b", "c"], "job2")
    assert [r["item_key"] for r in repo.pending("run1", "upload")] == ["b", "c"]


def test_pending_returns_failed_items_but_not_skipped(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b", "c"], "job1")
    repo.mark("run1", "upload", "a", "failed", {"why": "boom"})
    repo.mark("run1", "upload", "b", "skipped")
    keys = [r["item_key"] for r in repo.pending("run1", "upload")]
    assert keys == ["a", "c"]


def test_detail_round_trips_as_a_dict(conn):
    repo = JobItemsRepo(conn)
    repo.put("run1", "repack", "f1", "job1", "pending", {"to": "2025-01"})
    row = repo.pending("run1", "repack")[0]
    assert row["detail"] == {"to": "2025-01"}


def test_phases_and_runs_do_not_collide(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a"], "job1")
    repo.enumerate("run1", "repack", ["a"], "job1")
    repo.enumerate("run2", "upload", ["a"], "job1")
    repo.mark("run1", "upload", "a", "done")
    assert repo.pending("run1", "upload") == []
    assert len(repo.pending("run1", "repack")) == 1
    assert len(repo.pending("run2", "upload")) == 1


def test_counts_and_clear(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    repo.mark("run1", "upload", "a", "done")
    assert repo.counts("run1", "upload") == {"pending": 1, "done": 1}
    repo.clear("run1", "upload")
    assert repo.counts("run1", "upload") == {}


def test_unknown_state_is_rejected(conn):
    repo = JobItemsRepo(conn)
    with pytest.raises(ValueError):
        repo.put("run1", "upload", "a", "job1", "wobbly")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_job_items_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.db.job_items_repo'`

- [ ] **Step 3: Add the table to `schema.sql`**

Append to `photolib/db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS job_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    phase      TEXT NOT NULL,
    item_key   TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    state      TEXT NOT NULL CHECK (state IN ('pending','done','failed','skipped')),
    detail     TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, phase, item_key)
);

CREATE INDEX IF NOT EXISTS idx_job_items_run ON job_items(run_id, phase, state);
```

Bump `SCHEMA_VERSION` in `photolib/db/migrations.py` from `5` to `6`. No entry
in `_ADDED_COLUMNS` is needed — `migrate()` runs `schema.sql` first, and
`CREATE TABLE IF NOT EXISTS` creates the table on old catalogs too.

- [ ] **Step 4: Write the repo**

```python
# photolib/db/job_items_repo.py
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
        if state not in STATES:
            raise ValueError(f"unknown job item state: {state!r}")
        with self._lock:
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
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_job_items_repo.py tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/db/job_items_repo.py photolib/db/schema.sql photolib/db/migrations.py tests/test_job_items_repo.py
git commit -m "feat: job_items table and JobItemsRepo checkpoint ledger"
```

---

### Task 2: Run identity and item counts on jobs

**Files:**
- Modify: `photolib/db/schema.sql` (jobs table)
- Modify: `photolib/db/migrations.py:21-30` (`_ADDED_COLUMNS`)
- Modify: `photolib/db/jobs_repo.py`
- Test: `tests/test_jobs_repo.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `Job` model gains `run_id: str | None`, `resumed_from: str | None`, `phase: str | None`, `items_done: int`, `items_total: int`.
  - `JobsRepo.create(action: str, params: dict, run_id: str | None = None, resumed_from: str | None = None) -> Job` — generates a `run_id` when none is given.
  - `JobsRepo.mark_cancelled(job_id: str) -> None`.
  - `JobsRepo.update_progress(job_id, progress, message=None, phase=None, done=None, total=None) -> None` — `phase`/`done`/`total` are `COALESCE`d, so `None` leaves the stored value alone.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs_repo.py`:

```python
def test_create_generates_a_run_id(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    assert job.run_id
    assert repo.create("check_connection", {}).run_id != job.run_id


def test_create_accepts_an_explicit_run_id(conn):
    repo = JobsRepo(conn)
    first = repo.create("check_connection", {})
    second = repo.create("check_connection", {}, run_id=first.run_id,
                         resumed_from=first.id)
    assert second.run_id == first.run_id
    assert second.resumed_from == first.id


def test_mark_cancelled(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_cancelled(job.id)
    reloaded = repo.get(job.id)
    assert reloaded.status == "cancelled"
    assert reloaded.finished_at


def test_update_progress_records_phase_and_counts(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "half", phase="Upload (5/5)",
                         done=12, total=40)
    reloaded = repo.get(job.id)
    assert (reloaded.phase, reloaded.items_done, reloaded.items_total) == (
        "Upload (5/5)", 12, 40
    )


def test_update_progress_leaves_phase_alone_when_not_supplied(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "half", phase="Scan (2/5)", done=3, total=9)
    repo.update_progress(job.id, 0.6, "more")
    reloaded = repo.get(job.id)
    assert (reloaded.phase, reloaded.items_done, reloaded.items_total) == (
        "Scan (2/5)", 3, 9
    )
```

If `tests/test_jobs_repo.py` does not exist, create it with
`from photolib.db.jobs_repo import JobsRepo` at the top; the `conn` fixture
comes from `tests/conftest.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jobs_repo.py -v`
Expected: FAIL — `TypeError: create() got an unexpected keyword argument 'run_id'`

- [ ] **Step 3: Add the columns**

In `photolib/db/schema.sql`, the `jobs` table becomes:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    progress     REAL NOT NULL DEFAULT 0.0,
    message      TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    run_id       TEXT,
    resumed_from TEXT,
    phase        TEXT,
    items_done   INTEGER NOT NULL DEFAULT 0,
    items_total  INTEGER NOT NULL DEFAULT 0
);
```

In `photolib/db/migrations.py`, extend `_ADDED_COLUMNS`:

```python
    ("jobs", "run_id", "run_id TEXT"),
    ("jobs", "resumed_from", "resumed_from TEXT"),
    ("jobs", "phase", "phase TEXT"),
    ("jobs", "items_done", "items_done INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "items_total", "items_total INTEGER NOT NULL DEFAULT 0"),
```

- [ ] **Step 4: Update `JobsRepo`**

In `photolib/db/jobs_repo.py`, add to the `Job` model after `finished_at`:

```python
    run_id: str | None = None
    resumed_from: str | None = None
    phase: str | None = None
    items_done: int = 0
    items_total: int = 0
```

Replace `create`, add `mark_cancelled`, and replace `update_progress`:

```python
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

    def mark_cancelled(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? "
                "WHERE id = ?",
                (_now(), job_id),
            )
            self._conn.commit()

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
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_jobs_repo.py tests/test_migrations.py tests/test_api_jobs.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/db/jobs_repo.py photolib/db/schema.sql photolib/db/migrations.py tests/test_jobs_repo.py
git commit -m "feat: run identity, phase and item counts on jobs"
```

---

### Task 3: Phase-aware progress events

**Files:**
- Modify: `photolib/actions/base.py:24-31` (`ProgressEvent`), `:33-46` (`ActionContext`)
- Modify: `photolib/jobs/runner.py:69-97`
- Test: `tests/test_jobs_runner.py`

**Interfaces:**
- Consumes: `JobsRepo.update_progress(..., phase, done, total)` from Task 2.
- Produces:
  - `ProgressEvent(message, progress=None, level="info", phase=None, done=None, total=None)`.
  - `ActionContext` gains `run_id: str | None = None` and `cancelled: threading.Event | None = None`. The runner sets both before calling `run()`; `cancelled` stays `None` outside a job.
  - SSE `event` payloads gain `phase`, `done`, `total`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jobs_runner.py`:

```python
def test_phase_and_item_counts_reach_the_job_row(runner, conn, monkeypatch):
    from photolib.actions import registry

    def phased(ctx, params):
        yield ProgressEvent("starting", progress=0.1, phase="Scan (1/2)",
                            done=1, total=4)
        yield ProgressEvent("finishing", progress=0.9, phase="Upload (2/2)",
                            done=4, total=4)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", phased)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    runner.wait_idle()
    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.phase == "Upload (2/2)"
    assert (reloaded.items_done, reloaded.items_total) == (4, 4)


def test_run_id_reaches_the_action(runner, conn, monkeypatch):
    from photolib.actions import registry

    seen = {}

    def peek(ctx, params):
        seen["run_id"] = ctx.run_id
        seen["cancellable"] = ctx.cancelled is not None
        yield ProgressEvent("ok", progress=1.0)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", peek)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert seen["run_id"] == JobsRepo(conn).get(job.id).run_id
    assert seen["cancellable"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jobs_runner.py -v`
Expected: FAIL — `TypeError: ProgressEvent.__init__() got an unexpected keyword argument 'phase'`

- [ ] **Step 3: Extend `ProgressEvent` and `ActionContext`**

In `photolib/actions/base.py`, add `import threading` and replace the two
dataclasses' bodies:

```python
@dataclass
class ProgressEvent:
    """One unit of feedback from a running action."""

    message: str
    progress: float | None = None
    level: str = "info"
    phase: str | None = None
    """Display name of the phase, e.g. 'Upload (5/5)'. None outside a flow."""
    done: int | None = None
    total: int | None = None
    """Items finished and items declared, for the phase in progress."""


@dataclass
class ActionContext:
    """Everything an action is allowed to reach."""

    conn: sqlite3.Connection
    drive: object
    settings: SettingsRepo
    config: Config
    writer: object | None = None
    """Whatever may mutate Drive. None in a read-only context."""
    inflight: object | None = None
    """Where live transfers report themselves. None when nobody is watching."""
    run_id: str | None = None
    """Identity of this flow run; the key `job_items` are stored under."""
    cancelled: threading.Event | None = None
    """Set when the operator cancels. None outside a job."""
```

- [ ] **Step 4: Have the runner carry them**

In `photolib/jobs/runner.py`, replace `_execute`:

```python
    def _execute(self, job_id: str) -> None:
        job = self._repo.get(job_id)
        if job is None:
            return
        self._repo.mark_running(job_id)
        self._emit(job_id, {"type": "status", "status": "running"})
        try:
            spec = registry.get_action(job.action)
            params = spec.params_model.model_validate(job.params)
            ctx = self._context_factory()
            ctx.run_id = job.run_id
            for event in spec.run(ctx, params):
                self._repo.add_event(job_id, event.level, event.message)
                if event.progress is not None:
                    self._repo.update_progress(
                        job_id, event.progress, event.message,
                        phase=event.phase, done=event.done, total=event.total,
                    )
                self._emit(job_id, {
                    "type": "event", "level": event.level,
                    "message": event.message, "progress": event.progress,
                    "phase": event.phase, "done": event.done,
                    "total": event.total,
                })
            self._repo.mark_done(job_id)
            self._emit(job_id, {"type": "status", "status": "done"})
        except Exception as exc:
            detail = f"{exc}\n{traceback.format_exc()}"
            self._repo.mark_failed(job_id, detail)
            self._repo.add_event(job_id, "error", str(exc))
            self._emit(job_id, {
                "type": "status", "status": "failed", "error": str(exc),
            })
```

`ctx.cancelled` is wired in Task 4; the test above only asserts it is not
`None`, so set it here too for now:

```python
            ctx.cancelled = threading.Event()
```

with `import threading` at the top of the module.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_jobs_runner.py tests/test_actions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/actions/base.py photolib/jobs/runner.py tests/test_jobs_runner.py
git commit -m "feat: phase, item counts and run identity on progress events"
```

---

### Task 4: Cancel a running job

**Files:**
- Modify: `photolib/jobs/runner.py`
- Modify: `photolib/api/routes_jobs.py`
- Test: `tests/test_jobs_runner.py`, `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `JobsRepo.mark_cancelled` (Task 2), `ActionContext.cancelled` (Task 3).
- Produces:
  - `JobRunner.cancel(job_id: str) -> bool` — `True` when the job was queued or running, `False` when already finished or unknown.
  - `POST /api/jobs/{id}/cancel` → the job dict; `409` when the job is finished, `404` when unknown.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jobs_runner.py`:

```python
import threading


def test_cancelling_a_running_job_stops_it_and_keeps_checkpoints(
    runner, conn, monkeypatch
):
    from photolib.actions import registry
    from photolib.db.job_items_repo import JobItemsRepo

    started = threading.Event()
    closed = {}

    def slow(ctx, params):
        items = JobItemsRepo(ctx.conn)
        items.enumerate(ctx.run_id, "work", ["a", "b", "c"], "x")
        try:
            for key in ("a", "b", "c"):
                items.mark(ctx.run_id, "work", key, "done")
                started.set()
                yield ProgressEvent(key, progress=0.3)
        except GeneratorExit:
            closed["yes"] = True
            raise

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", slow)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    started.wait(timeout=5.0)
    runner.cancel(job.id)
    runner.wait_idle()

    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "cancelled"
    assert closed.get("yes") is True
    assert JobItemsRepo(conn).counts(reloaded.run_id, "work")["done"] >= 1


def test_cancelling_a_queued_job_never_starts_it(runner, conn, monkeypatch):
    from photolib.actions import registry

    ran = {}

    def never(ctx, params):
        ran["yes"] = True
        yield ProgressEvent("should not happen", progress=1.0)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", never)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    runner.stop()
    job = runner.submit("check_connection", {})
    assert runner.cancel(job.id) is True
    runner.start()
    runner.wait_idle()
    assert "yes" not in ran
    assert JobsRepo(conn).get(job.id).status == "cancelled"


def test_cancelling_a_finished_job_is_false(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert runner.cancel(job.id) is False
```

Append to `tests/test_api_jobs.py` (follow the existing client fixture in that
file):

```python
def test_cancel_route_rejects_a_finished_job(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_cancel_route_404s_on_an_unknown_job(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jobs_runner.py -k cancel -v`
Expected: FAIL — `AttributeError: 'JobRunner' object has no attribute 'cancel'`

- [ ] **Step 3: Implement cancellation in the runner**

In `photolib/jobs/runner.py`, add to `__init__`:

```python
        self._cancels: dict[str, threading.Event] = {}
        self._cancels_lock = threading.Lock()
```

Add the public method:

```python
    def cancel(self, job_id: str) -> bool:
        """Ask a queued or running job to stop. False if it is already over."""
        job = self._repo.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return False
        with self._cancels_lock:
            event = self._cancels.setdefault(job_id, threading.Event())
        event.set()
        if job.status == "queued":
            # It may never reach _execute if the runner is stopped; settle it
            # now. _execute re-checks and returns early if it does start.
            self._repo.mark_cancelled(job_id)
            self._emit(job_id, {"type": "status", "status": "cancelled"})
        return True
```

Replace the body of `_execute` between `params = ...` and `self._repo.mark_done`:

```python
            params = spec.params_model.model_validate(job.params)
            with self._cancels_lock:
                cancel = self._cancels.setdefault(job.id, threading.Event())
            if cancel.is_set():
                self._repo.mark_cancelled(job_id)
                self._emit(job_id, {"type": "status", "status": "cancelled"})
                return

            ctx = self._context_factory()
            ctx.run_id = job.run_id
            ctx.cancelled = cancel

            generator = spec.run(ctx, params)
            for event in generator:
                self._repo.add_event(job_id, event.level, event.message)
                if event.progress is not None:
                    self._repo.update_progress(
                        job_id, event.progress, event.message,
                        phase=event.phase, done=event.done, total=event.total,
                    )
                self._emit(job_id, {
                    "type": "event", "level": event.level,
                    "message": event.message, "progress": event.progress,
                    "phase": event.phase, "done": event.done,
                    "total": event.total,
                })
                if cancel.is_set():
                    # Closing raises GeneratorExit at the yield, so the
                    # action's `finally` blocks run and job_items survive.
                    generator.close()
                    self._repo.mark_cancelled(job_id)
                    self._repo.add_event(job_id, "warn", "Cancelled.")
                    self._emit(job_id, {
                        "type": "status", "status": "cancelled",
                    })
                    return
```

Add a `finally` to `_execute` that forgets the event:

```python
        finally:
            with self._cancels_lock:
                self._cancels.pop(job_id, None)
```

Note the existing `except Exception` must stay above this `finally`.

- [ ] **Step 4: Add the route**

In `photolib/api/routes_jobs.py`:

```python
@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    jobs = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if not request.app.state.runner.cancel(job_id):
        raise HTTPException(
            status_code=409, detail=f"job is already {job.status}"
        )
    return jobs.get(job_id).model_dump()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_jobs_runner.py tests/test_api_jobs.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/jobs/runner.py photolib/api/routes_jobs.py tests/test_jobs_runner.py tests/test_api_jobs.py
git commit -m "feat: cancel a queued or running job, preserving checkpoints"
```

---

### Task 5: Resume a failed or cancelled job

**Files:**
- Modify: `photolib/jobs/runner.py` (`submit`)
- Modify: `photolib/api/routes_jobs.py`
- Test: `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `JobsRepo.create(..., run_id, resumed_from)` (Task 2).
- Produces:
  - `JobRunner.submit(action_id: str, params: dict, run_id: str | None = None, resumed_from: str | None = None) -> Job`.
  - `POST /api/jobs/{id}/resume` → the new job dict; `409` unless the source job is `failed` or `cancelled`, `404` when unknown.
  - `GET /api/jobs/{id}/items?phase=&state=` → list of item dicts.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_jobs.py`:

```python
def test_resume_reuses_the_run_id_and_records_the_source(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    client.app.state.jobs.mark_failed(job["id"], "boom")

    resumed = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert resumed["run_id"] == job["run_id"]
    assert resumed["resumed_from"] == job["id"]
    assert resumed["id"] != job["id"]


def test_resume_rejects_a_successful_job(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 409


def test_resume_injects_run_id_only_when_the_action_declares_it(client):
    """check_connection has no run_id param; extra='forbid' would reject it."""
    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    client.app.state.jobs.mark_failed(job["id"], "boom")
    resumed = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert "run_id" not in resumed["params"]


def test_items_route_returns_the_ledger(client):
    from photolib.db.job_items_repo import JobItemsRepo

    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    JobItemsRepo(client.app.state.conn).enumerate(
        job["run_id"], "work", ["a", "b"], job["id"]
    )
    body = client.get(f"/api/jobs/{job['id']}/items?phase=work").json()
    assert [i["item_key"] for i in body] == ["a", "b"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_jobs.py -k "resume or items" -v`
Expected: FAIL — 405 Method Not Allowed on `/resume`

- [ ] **Step 3: Widen `submit`**

In `photolib/jobs/runner.py`:

```python
    def submit(
        self,
        action_id: str,
        params: dict,
        run_id: str | None = None,
        resumed_from: str | None = None,
    ) -> Job:
        registry.get_action(action_id)  # fail fast on unknown ids
        job = self._repo.create(action_id, params, run_id, resumed_from)
        with self._outstanding_lock:
            self._outstanding += 1
            self._idle.clear()
        self._queue.put(job.id)
        return job
```

- [ ] **Step 4: Add the routes**

In `photolib/api/routes_jobs.py`:

```python
from photolib.actions.registry import UnknownActionError, get_action
from photolib.db.job_items_repo import JobItemsRepo

RESUMABLE = {"failed", "cancelled"}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if job.status not in RESUMABLE:
        raise HTTPException(
            status_code=409,
            detail=f"only failed or cancelled jobs resume; this one is {job.status}",
        )
    try:
        spec = get_action(job.action)
    except UnknownActionError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown action: {job.action}"
        ) from exc

    params = dict(job.params)
    # ActionParams forbids extras, so only actions that declare run_id get it.
    if "run_id" in spec.params_model.model_fields:
        params["run_id"] = job.run_id

    resumed = request.app.state.runner.submit(
        job.action, params, run_id=job.run_id, resumed_from=job.id
    )
    return resumed.model_dump()


@router.get("/jobs/{job_id}/items")
def job_items(
    job_id: str,
    request: Request,
    phase: str | None = None,
    state: str | None = None,
) -> list[dict]:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return JobItemsRepo(request.app.state.conn).all(job.run_id, phase, state)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api_jobs.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/jobs/runner.py photolib/api/routes_jobs.py tests/test_api_jobs.py
git commit -m "feat: resume a failed or cancelled job onto the same run"
```

---

### Task 6: Flows and Advanced in the registry and the Nav

**Files:**
- Modify: `photolib/actions/base.py` (`ActionSpec`)
- Modify: `photolib/actions/registry.py`
- Modify: `photolib/actions/reorganize.py` (`TITLE`)
- Modify: `photolib/api/routes_actions.py:14-25`
- Modify: `web/src/api/types.ts`, `web/src/components/Nav.tsx`
- Test: `tests/test_actions.py`, `tests/test_api_actions.py`, `web/src/components/Nav.test.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ActionSpec.group: str` — `"flow"` or `"advanced"`, read from an optional module-level `GROUP`, defaulting to `"advanced"`.
  - `all_actions()` sorts by `(group != "flow", order, id)` so flows come first.
  - `GET /api/actions` items gain `"group"`.
  - TS `ActionSpec` gains `group: 'flow' | 'advanced'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_actions.py`:

```python
def test_actions_default_to_the_advanced_group():
    from photolib.actions.registry import get_action

    assert get_action("scan_archives").group == "advanced"


def test_flows_sort_ahead_of_advanced_actions():
    from photolib.actions.registry import all_actions

    groups = [spec.group for spec in all_actions()]
    assert groups == sorted(groups, key=lambda g: g != "flow")


def test_reorganize_is_retitled_repack_buckets():
    from photolib.actions.registry import get_action

    assert get_action("reorganize").title == "Repack Buckets"
```

Append to `tests/test_api_actions.py`:

```python
def test_action_list_exposes_the_group(client):
    body = client.get("/api/actions").json()
    assert all("group" in spec for spec in body)
```

Append to `web/src/components/Nav.test.tsx`:

```tsx
it('separates flows from advanced actions', () => {
  const actions = [
    { id: 'sync_archives', title: 'Sync from Archives', description: '', order: 1, group: 'flow', schema: { type: 'object' } },
    { id: 'scan_archives', title: 'Scan Archives', description: '', order: 10, group: 'advanced', schema: { type: 'object' } },
  ] as ActionSpec[]
  render(<MemoryRouter><Nav actions={actions} /></MemoryRouter>)
  expect(screen.getByRole('heading', { name: 'Flows' })).toBeInTheDocument()
  expect(screen.getByText('Advanced')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Sync from Archives' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_actions.py -k group -v`
Expected: FAIL — `AttributeError: 'ActionSpec' object has no attribute 'group'`

- [ ] **Step 3: Add `group` to the spec and registry**

In `photolib/actions/base.py`, `ActionSpec` gains `group` as its **last**
field — it is a plain dataclass, so a defaulted field cannot precede
`params_model` and `run`, which have no defaults:

```python
@dataclass
class ActionSpec:
    id: str
    title: str
    description: str
    order: int
    params_model: type[ActionParams]
    run: Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]
    group: str = "advanced"
```

In `photolib/actions/registry.py`, pass it through in `_discover`:

```python
        specs[module.ID] = ActionSpec(
            id=module.ID,
            title=module.TITLE,
            description=module.DESCRIPTION,
            order=module.ORDER,
            params_model=module.Params,
            run=module.run,
            group=getattr(module, "GROUP", "advanced"),
        )
```

and change the sort:

```python
def all_actions() -> list[ActionSpec]:
    """Flows first, then the advanced steps, each block by order then id."""
    return sorted(
        _discover().values(),
        key=lambda s: (s.group != "flow", s.order, s.id),
    )
```

In `photolib/actions/reorganize.py`, change `TITLE` to `"Repack Buckets"`.

In `photolib/api/routes_actions.py`, add `"group": spec.group,` to the dict
built by `list_actions`.

- [ ] **Step 4: Update the frontend**

In `web/src/api/types.ts`, `ActionSpec` gains `group: 'flow' | 'advanced'`.

Replace the Actions section of `web/src/components/Nav.tsx`:

```tsx
export function Nav({ actions }: { actions: ActionSpec[] }) {
  const flows = actions.filter((a) => a.group === 'flow')
  const advanced = actions.filter((a) => a.group !== 'flow')

  return (
    <nav className="nav">
      <h1>Photo Library</h1>
      <section>
        <h2>Setup</h2>
        <NavLink to="/settings">Settings</NavLink>
      </section>
      <section>
        <h2>Flows</h2>
        {flows.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
      <section>
        <h2>Browse</h2>
        <NavLink to="/library">Library</NavLink>
        <NavLink to="/tags">Tags</NavLink>
      </section>
      <section>
        <h2>Activity</h2>
        <NavLink to="/review">Review Plan</NavLink>
        <NavLink to="/jobs">Jobs</NavLink>
      </section>
      <section>
        <details>
          <summary>Advanced</summary>
          {advanced.map((action) => (
            <NavLink key={action.id} to={`/actions/${action.id}`}>
              {action.title}
            </NavLink>
          ))}
        </details>
      </section>
    </nav>
  )
}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_actions.py tests/test_api_actions.py -v && cd web && npm test && cd ..`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/actions/base.py photolib/actions/registry.py photolib/actions/reorganize.py photolib/api/routes_actions.py web/src tests/test_actions.py tests/test_api_actions.py
git commit -m "feat: group actions into flows and advanced steps"
```

---

### Task 7: The phase helper

**Files:**
- Create: `photolib/actions/phases.py`
- Test: `tests/test_phases.py`

**Interfaces:**
- Consumes: `ProgressEvent` with `phase`/`done`/`total` (Task 3).
- Produces:
  - `run_phase(name, span, runner, ctx, params, *, index, total) -> Iterator[ProgressEvent]`
    where `span` is `tuple[float, float]`, `runner` is
    `Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]`,
    `index`/`total` are 1-based phase position and count.
  - `phase_label(name: str, index: int, total: int) -> str` → `"Scan (2/5)"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_phases.py
from photolib.actions.base import ProgressEvent
from photolib.actions.phases import phase_label, run_phase


def _sub(events):
    def runner(ctx, params):
        yield from events
    return runner


def test_progress_is_rescaled_into_the_span():
    events = [ProgressEvent("a", progress=0.0), ProgressEvent("b", progress=1.0)]
    out = list(run_phase("Scan", (0.2, 0.6), _sub(events), None, None,
                         index=2, total=5))
    assert [e.progress for e in out] == [0.2, 0.6]


def test_midpoint_lands_in_the_middle_of_the_span():
    events = [ProgressEvent("a", progress=0.5)]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=1, total=1))
    assert out[0].progress == 0.5


def test_none_progress_passes_through():
    events = [ProgressEvent("a")]
    out = list(run_phase("Scan", (0.2, 0.6), _sub(events), None, None,
                         index=2, total=5))
    assert out[0].progress is None


def test_phase_label_is_attached_and_the_message_is_untouched():
    events = [ProgressEvent("indexing IMG_1.HEIC", progress=0.5, level="warn")]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=2, total=5))
    assert out[0].phase == "Scan (2/5)"
    assert out[0].message == "indexing IMG_1.HEIC"
    assert out[0].level == "warn"


def test_item_counts_pass_through():
    events = [ProgressEvent("a", progress=0.5, done=3, total=9)]
    out = list(run_phase("Scan", (0.0, 1.0), _sub(events), None, None,
                         index=1, total=1))
    assert (out[0].done, out[0].total) == (3, 9)


def test_phase_label_format():
    assert phase_label("Upload", 5, 5) == "Upload (5/5)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_phases.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.actions.phases'`

- [ ] **Step 3: Write the helper**

```python
# photolib/actions/phases.py
"""Compose actions into flows without either side knowing about the other.

A flow drives an existing action's `run()` generator through `run_phase`,
which rescales that action's 0..1 progress into its slice of the flow and
stamps the phase name onto every event. The sub-action stays unaware it is
being composed, so its Advanced page keeps working on the same code.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

PhaseRunner = Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]


def phase_label(name: str, index: int, total: int) -> str:
    return f"{name} ({index}/{total})"


def run_phase(
    name: str,
    span: tuple[float, float],
    runner: PhaseRunner,
    ctx: ActionContext,
    params: ActionParams,
    *,
    index: int,
    total: int,
) -> Iterator[ProgressEvent]:
    """Re-yield `runner`'s events with progress mapped into `span`."""
    low, high = span
    label = phase_label(name, index, total)
    for event in runner(ctx, params):
        progress = event.progress
        if progress is not None:
            progress = low + (high - low) * min(max(progress, 0.0), 1.0)
        yield replace(event, progress=progress, phase=label)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_phases.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/phases.py tests/test_phases.py
git commit -m "feat: run_phase composes sub-actions into flows"
```

---

### Task 8: A shared, resumable destination walk

**Files:**
- Modify: `photolib/drive/client.py:19-22` (`FILE_FIELDS`), `:29-60` (`DriveFile`)
- Create: `photolib/scan.py`
- Modify: `photolib/actions/scan_archives.py:30-51`
- Test: `tests/test_scan_destination.py`, `tests/test_drive_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `DriveFile.app_properties: dict | None` (alias `appProperties`).
  - `DriveFile.location() -> tuple[float, float] | None` — from `imageMediaMetadata.location`, `None` when absent or when both values are zero.
  - `photolib.scan.index_destination(drive, conn, folder_id: str, *, done: dict[str, list[list[str]]] | None = None, on_folder: Callable[[str, list[list[str]]], None] | None = None) -> int` — walks, upserts `drive_files`, returns the file count. `done` maps an already-walked folder id to its child folders as `[id, name]` pairs; those folders are not listed again, but their children are still traversed *and* keep their correct path, because the name travels with the id. `on_folder(folder_id, child_folders)` fires after each folder is listed. JSON has no tuples, so pairs are lists — they round-trip through `job_items.detail` unchanged.
  - `scan_archives._index_destination(ctx, folder_id)` becomes a one-line call to it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scan_destination.py
from photolib.scan import index_destination
from tests.fakes.fake_drive import FakeDrive


def _tree() -> FakeDrive:
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("m1", "2025-01", parent="root")
    drive.add_folder("nested", "back_2019", parent="root")
    drive.add_folder("deep", "inner", parent="nested")
    drive.add_file("f1", "a.heic", b"aaa", parent="m1", mime_type="image/heic")
    drive.add_file("f2", "b.mov", b"bbb", parent="deep", mime_type="video/quicktime")
    return drive


def test_walks_every_depth_and_records_paths(conn):
    drive = _tree()
    assert index_destination(drive, conn, "root") == 2
    rows = {
        r["drive_id"]: r["parent_path"]
        for r in conn.execute("SELECT drive_id, parent_path FROM drive_files")
    }
    assert rows == {"f1": "2025-01", "f2": "back_2019/inner"}


def test_on_folder_reports_child_folders_as_id_name_pairs(conn):
    drive = _tree()
    seen: dict[str, list[list[str]]] = {}
    index_destination(drive, conn, "root",
                      on_folder=lambda fid, kids: seen.__setitem__(fid, kids))
    assert sorted(seen["root"]) == [["m1", "2025-01"], ["nested", "back_2019"]]
    assert seen["deep"] == []


def test_done_folders_are_not_relisted_but_children_still_walk(conn):
    drive = _tree()
    calls: list[str] = []
    original = drive.list_children
    drive.list_children = lambda fid: (calls.append(fid), original(fid))[1]

    index_destination(drive, conn, "root",
                      done={"nested": [["deep", "inner"]]})
    assert "nested" not in calls
    assert "deep" in calls


def test_a_skipped_folder_still_gives_its_children_the_right_path(conn):
    """Skipping must not flatten paths — the name travels with the id."""
    drive = _tree()
    index_destination(drive, conn, "root", done={"nested": [["deep", "inner"]]})
    row = conn.execute(
        "SELECT parent_path FROM drive_files WHERE drive_id = 'f2'"
    ).fetchone()
    assert row["parent_path"] == "back_2019/inner"
```

Append to `tests/test_drive_client.py`:

```python
def test_location_reads_image_metadata():
    from photolib.drive.client import DriveFile

    file = DriveFile(
        id="f", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"location": {"latitude": 37.9, "longitude": 23.7}},
    )
    assert file.location() == (37.9, 23.7)


def test_location_is_none_when_absent_or_null_island():
    from photolib.drive.client import DriveFile

    bare = DriveFile(id="f", name="a.heic", mimeType="image/heic")
    assert bare.location() is None
    zeroed = DriveFile(
        id="f", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"location": {"latitude": 0.0, "longitude": 0.0}},
    )
    assert zeroed.location() is None


def test_file_fields_request_location_and_app_properties():
    from photolib.drive.client import FILE_FIELDS

    assert "imageMediaMetadata(time,location)" in FILE_FIELDS
    assert "appProperties" in FILE_FIELDS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scan_destination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.scan'`

- [ ] **Step 3: Extend the Drive client**

In `photolib/drive/client.py`, replace `FILE_FIELDS`:

```python
FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,createdTime,modifiedTime,parents,"
    "thumbnailLink,imageMediaMetadata(time,location),appProperties"
)
```

Add to `DriveFile` after `image_media_metadata`:

```python
    app_properties: dict | None = Field(default=None, alias="appProperties")
```

and a method after `capture_hint`:

```python
    def location(self) -> tuple[float, float] | None:
        """EXIF coordinates, or None. (0, 0) is Drive's way of saying nothing."""
        loc = (self.image_media_metadata or {}).get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return float(lat), float(lon)
```

- [ ] **Step 4: Extract the walk**

```python
# photolib/scan.py
"""The destination walk, shared by Scan Archives and Reorganize Folders.

Listing a folder is one API call, and a large library has dozens, so the walk
reports each folder it finishes and can be told which folders a previous run
already covered. A skipped folder still yields its subfolders — the caller
recorded them — because the walk discovers structure only by listing.
"""

from __future__ import annotations

from typing import Callable

from photolib.db.scan_repo import ScanRepo


def index_destination(
    drive,
    conn,
    folder_id: str,
    *,
    done: dict[str, list[list[str]]] | None = None,
    on_folder: Callable[[str, list[list[str]]], None] | None = None,
) -> int:
    """Walk `folder_id` at any depth, upsert every file, return the count."""
    already = done or {}
    repo = ScanRepo(conn)
    stack: list[tuple[str, str]] = [(folder_id, "")]
    seen: set[str] = set()
    total = 0

    while stack:
        current, path = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        if current in already and current != folder_id:
            # Its files were upserted last time. Its subfolders were not
            # necessarily walked, and their paths depend on names only a
            # listing reveals — so the name was recorded alongside the id.
            for child_id, child_name in already[current]:
                stack.append(
                    (child_id, f"{path}/{child_name}" if path else child_name)
                )
            continue

        child_folders: list[list[str]] = []
        rows: list[dict] = []
        for child in drive.list_children(current):
            if child.is_folder:
                child_folders.append([child.id, child.name])
                stack.append(
                    (child.id, f"{path}/{child.name}" if path else child.name)
                )
                continue
            rows.append(
                {
                    "drive_id": child.id, "name": child.name,
                    "parent_path": path, "md5": child.md5,
                    "size": child.size, "mime_type": child.mime_type,
                    "capture_hint": child.capture_hint(),
                }
            )

        # Commit this folder's rows *before* reporting it finished. A caller
        # that checkpoints on `on_folder` would otherwise skip a folder whose
        # rows were still in memory when the run died.
        repo.upsert_drive_files(rows)
        total += len(rows)
        if on_folder:
            on_folder(current, child_folders)

    return total
```

A skipped folder's `drive_files` rows were written last time and are not
rewritten; only its listing call is saved. `on_folder` fires strictly after
that folder's rows are committed, so a folder is never marked done on the
strength of a walk that did not finish.

In `photolib/actions/scan_archives.py`, replace `_index_destination` with:

```python
def _index_destination(ctx: ActionContext, folder_id: str) -> int:
    """Walk the destination at any depth and return how many files were seen."""
    return index_destination(ctx.drive, ctx.conn, folder_id)
```

and add `from photolib.scan import index_destination` to the imports. Drop the
now-unused `ScanRepo` import only if nothing else in the module uses it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_scan_destination.py tests/test_drive_client.py tests/test_action_scan.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/scan.py photolib/drive/client.py photolib/actions/scan_archives.py tests/test_scan_destination.py tests/test_drive_client.py
git commit -m "feat: share the destination walk and read EXIF location + appProperties"
```

---

### Task 9: Plan verdict columns

**Files:**
- Modify: `photolib/db/schema.sql` (media table)
- Modify: `photolib/db/migrations.py` (`_ADDED_COLUMNS`)
- Modify: `photolib/db/media_repo.py:18-22` (`_PLAN_FIELDS`), `:32-43` (`_UPLOAD_SELECT`), `summary`, `pending_uploads`
- Test: `tests/test_media_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `media.plan_verdict TEXT` (`'skip' | 'verify' | 'upload'`, or `NULL` on an unplanned row) and `media.plan_match TEXT` (the Drive file id the verdict refers to). Both are in `_PLAN_FIELDS`, so `clear_plan()` resets them.
  - `MediaRepo.verified_by_crc() -> dict[tuple[int, int], sqlite3.Row]` — verified uploads keyed by `(crc32, entry size)`.
  - `MediaRepo.summary()` gains `"skipped"`; its `"pending"` excludes skipped rows.
  - `pending_uploads()` excludes rows whose `plan_verdict` is `'skip'`.
  - `_UPLOAD_SELECT` exposes `plan_verdict`, `plan_match` and `match_md5` (the matched Drive file's MD5, via a `LEFT JOIN drive_files`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media_repo.py` (reuse whatever helper that file already
has for inserting an archive + entry + media row; the shape below assumes a
`_seed(conn, ...)` you may need to write once):

```python
def test_skipped_rows_are_not_offered_for_upload(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01", target_name="a.heic",
                  plan_verdict="skip", plan_match="drive-1")
    assert repo.pending_uploads() == []


def test_verify_rows_are_offered_with_the_match_md5(conn, seeded_entry):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size) "
        "VALUES ('drive-9', 'a.heic', '2025-01', 'abc123', 3)"
    )
    conn.commit()
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01",
                  target_name="a~0000ff.heic", plan_verdict="verify",
                  plan_match="drive-9")
    row = repo.pending_uploads()[0]
    assert row["plan_verdict"] == "verify"
    assert row["match_md5"] == "abc123"


def test_summary_separates_skipped_from_pending(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01", target_name="a.heic",
                  plan_verdict="skip", plan_match="drive-1")
    summary = repo.summary()
    assert summary["skipped"] == 1
    assert summary["pending"] == 0


def test_verified_by_crc_keys_on_crc_and_size(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.mark_uploaded(seeded_entry, "drive-7", "deadbeef")
    key = next(iter(repo.verified_by_crc()))
    assert isinstance(key, tuple) and len(key) == 2
    assert repo.verified_by_crc()[key]["drive_file_id"] == "drive-7"


def test_clear_plan_resets_the_verdict(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, plan_verdict="skip", plan_match="drive-1")
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["plan_verdict"] is None and row["plan_match"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media_repo.py -k "verdict or skipped or verified_by_crc" -v`
Expected: FAIL — `ValueError: unknown planning field(s): ['plan_match', 'plan_verdict']`

- [ ] **Step 3: Add the columns**

In `photolib/db/schema.sql`, add to the `media` table after `duplicate_reason`:

```sql
    plan_verdict     TEXT,
    plan_match       TEXT,
```

No CHECK constraint: `ALTER TABLE ADD COLUMN` and `schema.sql` must produce
identical tables, and `tests/test_migrations.py` asserts it. The three values
are validated where they are written, in `plan_organize`.

In `photolib/db/migrations.py`, extend `_ADDED_COLUMNS`:

```python
    ("media", "plan_verdict", "plan_verdict TEXT"),
    ("media", "plan_match", "plan_match TEXT"),
```

- [ ] **Step 4: Teach `MediaRepo` about them**

In `photolib/db/media_repo.py`:

```python
_PLAN_FIELDS = (
    "capture_time", "capture_source", "latitude", "longitude",
    "country", "target_folder", "target_name",
    "duplicate_of", "duplicate_reason",
    "plan_verdict", "plan_match",
)
```

`_UPLOAD_SELECT` gains the verdict columns and the match's MD5:

```python
_UPLOAD_SELECT = """
    SELECT m.id AS media_id, m.entry_id, m.target_folder, m.target_name,
           m.capture_time, m.country, m.upload_status,
           m.upload_session_uri, m.upload_offset, m.session_started_at,
           m.attempts, m.plan_verdict, m.plan_match,
           m.drive_file_id, m.md5,
           e.path, e.name, e.crc32, e.size, e.compressed_size,
           e.method, e.local_header_offset,
           a.drive_id AS archive_drive_id, a.name AS archive_name,
           df.md5 AS match_md5
    FROM media m
    JOIN entries e ON e.id = m.entry_id
    JOIN archives a ON a.id = e.archive_id
    LEFT JOIN drive_files df ON df.drive_id = m.plan_match
"""
```

`pending_uploads` gains one clause:

```python
        sql = (
            f"{_UPLOAD_SELECT} WHERE m.upload_status IN ({placeholders}) "
            "AND m.target_folder IS NOT NULL "
            "AND COALESCE(m.plan_verdict, 'upload') != 'skip' "
            "ORDER BY a.name, e.local_header_offset"
        )
```

`summary` gains a key and narrows `pending`:

```python
            "pending": one(
                "SELECT COUNT(*) FROM media WHERE upload_status = 'pending' "
                "AND COALESCE(plan_verdict, 'upload') != 'skip'"
            ),
            "skipped": one(
                "SELECT COUNT(*) FROM media WHERE plan_verdict = 'skip'"
            ),
```

And a new query:

```python
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
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_media_repo.py tests/test_migrations.py tests/test_action_organize.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/db/media_repo.py photolib/db/schema.sql photolib/db/migrations.py tests/test_media_repo.py
git commit -m "feat: plan_verdict and plan_match columns on media"
```

---

### Task 10: Plan decides what is already there

**Files:**
- Modify: `photolib/actions/plan_organize.py`
- Modify: `photolib/db/scan_repo.py` (add `live_drive_ids`)
- Test: `tests/test_action_plan.py`

**Interfaces:**
- Consumes: `MediaRepo.verified_by_crc()` and the plan columns (Task 9).
- Produces:
  - `ScanRepo.live_drive_ids() -> set[str]` — drive ids with `trashed_at IS NULL`.
  - `plan_organize.verdict_for(row, verified_by_crc, live_ids, by_name) -> tuple[str, str | None]` returning `("skip"|"verify"|"upload", drive_id_or_None)`.
  - Rows with verdict `verify` get a disambiguated `target_name`, because a live file of that name already occupies the destination.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_action_plan.py`:

```python
from photolib.actions.plan_organize import verdict_for


class _Row(dict):
    """sqlite3.Row is read-only; a dict subscripts the same way."""


def test_verdict_skips_bytes_this_app_already_uploaded():
    row = _Row(crc32=111, entry_size=3, name="a.heic")
    verified = {(111, 3): _Row(drive_file_id="d1")}
    assert verdict_for(row, verified, {"d1"}, {}) == ("skip", "d1")


def test_verdict_does_not_skip_when_the_uploaded_copy_is_gone():
    row = _Row(crc32=111, entry_size=3, name="a.heic")
    verified = {(111, 3): _Row(drive_file_id="d1")}
    assert verdict_for(row, verified, set(), {}) == ("upload", None)


def test_verdict_defers_to_transfer_on_a_name_and_size_match():
    row = _Row(crc32=111, entry_size=3, name="a.heic")
    by_name = {"a.heic": [_Row(drive_id="d2", size=3)]}
    assert verdict_for(row, {}, {"d2"}, by_name) == ("verify", "d2")


def test_verdict_uploads_when_the_name_matches_but_the_size_differs():
    row = _Row(crc32=111, entry_size=3, name="a.heic")
    by_name = {"a.heic": [_Row(drive_id="d2", size=9)]}
    assert verdict_for(row, {}, {"d2"}, by_name) == ("upload", None)


def test_verdict_ignores_a_trashed_name_match():
    row = _Row(crc32=111, entry_size=3, name="a.heic")
    by_name = {"a.heic": [_Row(drive_id="d2", size=3)]}
    assert verdict_for(row, {}, set(), by_name) == ("upload", None)


def test_plan_disambiguates_the_name_of_a_verify_row(conn, planned_catalog):
    """A verify row's upload, if it happens, cannot reuse the taken name."""
    from photolib.db.media_repo import MediaRepo

    rows = [r for r in MediaRepo(conn).all_media()
            if r["plan_verdict"] == "verify"]
    assert rows, "fixture must produce at least one verify row"
    assert all("~" in r["target_name"] for r in rows)
```

`planned_catalog` is a fixture you add to `tests/test_action_plan.py`: seed one
archive with two entries, insert a live `drive_files` row whose `name` and
`size` match the first entry, then run `plan_organize.run` to completion
through a fake context. Follow the context construction already used by the
other tests in this file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_plan.py -k verdict -v`
Expected: FAIL — `ImportError: cannot import name 'verdict_for'`

- [ ] **Step 3: Add `live_drive_ids`**

In `photolib/db/scan_repo.py`:

```python
    def live_drive_ids(self) -> set[str]:
        """Drive ids the last scan saw untrashed."""
        with self._lock:
            return {
                row["drive_id"]
                for row in self._conn.execute(
                    "SELECT drive_id FROM drive_files WHERE trashed_at IS NULL"
                )
            }
```

- [ ] **Step 4: Decide the verdict in Plan**

In `photolib/actions/plan_organize.py`, add above `run`:

```python
def verdict_for(row, verified_by_crc, live_ids, by_name):
    """What Sync should do with this entry: skip, verify at transfer, upload.

    `skip` is certainty bought for nothing: this app uploaded these exact
    bytes and Drive confirmed them, and the file is still there. `verify`
    means a live file of the same name and size exists but its MD5 cannot be
    compared to a ZIP's CRC32 without the bytes, so the decision is deferred
    to the moment the bytes are in hand anyway.
    """
    hit = verified_by_crc.get((row["crc32"], row["entry_size"]))
    if hit is not None and hit["drive_file_id"] in live_ids:
        return "skip", hit["drive_file_id"]

    for candidate in by_name.get(row["name"], []):
        if candidate["drive_id"] not in live_ids:
            continue
        if candidate["size"] == row["entry_size"]:
            return "verify", candidate["drive_id"]

    return "upload", None
```

Inside `run`, after `existing = scan_repo.drive_file_names()`, add:

```python
    verified_by_crc = media_repo.verified_by_crc()
    live_ids = scan_repo.live_drive_ids()
```

In the second loop, replace the name-taking block and add the verdict:

```python
        verdict, match = verdict_for(row, verified_by_crc, live_ids, existing)

        month = buckets.month_of(capture)
        folder = fmap[month] if month else buckets.UNKNOWN_FOLDER
        name = row["name"]
        # A `verify` row's destination name is already occupied by the file it
        # matched. If the MD5s disagree at transfer time this uploads, so it
        # must upload under a free name; if they agree, the name is unused.
        if verdict == "verify" or (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        taken.add((folder, name))
```

and extend the `set_plan` call with:

```python
            plan_verdict=verdict,
            plan_match=match,
```

Count the verdicts alongside `duplicates` and report them:

```python
    detail = f"Planned {total} file(s)."
    if skipped:
        detail += f" {skipped} already in the destination (nothing to upload)."
    if to_verify:
        detail += f" {to_verify} will be checked against an existing file."
```

initialising `skipped = to_verify = 0` next to `duplicates = located = unknown_dates = 0`
and incrementing them in the loop.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_action_plan.py tests/test_api_review.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/actions/plan_organize.py photolib/db/scan_repo.py tests/test_action_plan.py
git commit -m "feat: plan decides skip, verify or upload from content evidence"
```

---

### Task 11: Transfer settles a verify, Organize honours it

**Files:**
- Modify: `photolib/transfer.py:44-48` (`TransferResult`), `transfer_entry`
- Modify: `photolib/actions/organize.py`
- Test: `tests/test_transfer.py`, `tests/test_action_organize.py`

**Interfaces:**
- Consumes: `plan_verdict`, `plan_match`, `match_md5` on `pending_uploads` rows (Task 9).
- Produces:
  - `TransferResult` gains `adopted: bool = False`.
  - `transfer_entry(..., skip_if_md5: str | None = None, adopt_id: str | None = None)` — when the inflated file's MD5 equals `skip_if_md5`, no session is opened and no bytes are sent; it returns `TransferResult(drive_file_id=adopt_id, md5=local_md5, size=entry.size, adopted=True)`.
  - Organize marks an adopted row `done` against the existing Drive file and does **not** call `record_drive_file` — the row is already in `drive_files`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transfer.py` (reuse the module's existing
`read_range`/entry builders):

```python
def test_matching_md5_adopts_the_existing_file_without_uploading(
    zip_entry, read_range, tmp_path
):
    import hashlib

    from photolib.transfer import transfer_entry
    from tests.fakes.fake_drive import FakeDrive

    drive = FakeDrive()
    expected = hashlib.md5(zip_entry.content).hexdigest()

    result = transfer_entry(
        read_range=read_range, entry=zip_entry.entry, writer=drive,
        parent_id="root", name="a.heic", properties={},
        spool_dir=tmp_path, skip_if_md5=expected, adopt_id="drive-existing",
    )
    assert result.adopted is True
    assert result.drive_file_id == "drive-existing"
    assert result.md5 == expected
    assert drive.sessions_started == 0


def test_differing_md5_uploads_normally(zip_entry, read_range, tmp_path):
    from photolib.transfer import transfer_entry
    from tests.fakes.fake_drive import FakeDrive

    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    result = transfer_entry(
        read_range=read_range, entry=zip_entry.entry, writer=drive,
        parent_id="root", name="a.heic", properties={},
        spool_dir=tmp_path, skip_if_md5="not-the-same", adopt_id="drive-x",
    )
    assert result.adopted is False
    assert result.drive_file_id != "drive-x"


def test_spool_is_always_deleted_after_an_adoption(
    zip_entry, read_range, tmp_path
):
    import hashlib

    from photolib.transfer import transfer_entry
    from tests.fakes.fake_drive import FakeDrive

    transfer_entry(
        read_range=read_range, entry=zip_entry.entry, writer=FakeDrive(),
        parent_id="root", name="a.heic", properties={}, spool_dir=tmp_path,
        skip_if_md5=hashlib.md5(zip_entry.content).hexdigest(),
        adopt_id="drive-existing",
    )
    assert list(tmp_path.iterdir()) == []
```

`FakeDrive` needs a `sessions_started` counter: add
`self.sessions_started = 0` to its `__init__` and increment it in
`start_session`.

Append to `tests/test_action_organize.py`:

```python
def test_skip_rows_are_never_uploaded(organize_context, conn):
    from photolib.db.media_repo import MediaRepo

    repo = MediaRepo(conn)
    for row in repo.all_media():
        repo.set_plan(row["entry_id"], plan_verdict="skip",
                      plan_match="drive-existing")
    events = list(organize.run(organize_context, organize.Params()))
    assert any("Nothing to upload" in e.message for e in events)


def test_verify_row_matching_drive_is_marked_done_against_that_file(
    organize_context, conn, drive, archive_content
):
    """The bytes came down to prove identity; none went up."""
    import hashlib

    from photolib.db.media_repo import MediaRepo

    repo = MediaRepo(conn)
    row = repo.all_media()[0]
    twin_md5 = hashlib.md5(archive_content[row["name"]]).hexdigest()
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type) "
        "VALUES ('drive-twin', ?, '2025-01', ?, ?, 'image/heic')",
        (row["name"], twin_md5, row["entry_size"]),
    )
    conn.commit()
    repo.set_plan(row["entry_id"], plan_verdict="verify",
                  plan_match="drive-twin")

    list(organize.run(organize_context, organize.Params()))

    after = repo.all_media()[0]
    assert after["upload_status"] == "done"
    assert after["drive_file_id"] == "drive-twin"
    assert drive.sessions_started == 0
```

`organize_context` and `drive` already exist in `tests/test_action_organize.py`
in some form — reuse whatever that file builds. `archive_content` is a new
fixture returning `{entry_name: raw_bytes}` for the archive the context was
built from; the zip builder in `tests/fixtures/zipbuilder.py` already holds
those bytes, so return them rather than re-inflating.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transfer.py -k "adopt or md5" -v`
Expected: FAIL — `TypeError: transfer_entry() got an unexpected keyword argument 'skip_if_md5'`

- [ ] **Step 3: Teach `transfer_entry` to adopt**

In `photolib/transfer.py`, extend the result:

```python
@dataclass
class TransferResult:
    drive_file_id: str
    md5: str
    size: int
    adopted: bool = False
    """True when the file was already in Drive and no bytes were uploaded."""
```

Add the two keyword parameters to `transfer_entry`'s signature after
`on_progress`:

```python
    skip_if_md5: str | None = None,
    adopt_id: str | None = None,
```

and insert immediately after `local_md5 = file_md5(spooled)`:

```python
        if skip_if_md5 is not None and adopt_id is not None:
            if local_md5 == skip_if_md5:
                # The bytes are already in Drive under `adopt_id`. Downloading
                # was the only way to know; uploading them again would be
                # pure waste. The `finally` below deletes the spool.
                return TransferResult(
                    drive_file_id=adopt_id, md5=local_md5,
                    size=entry.size, adopted=True,
                )
```

- [ ] **Step 4: Wire it into Organize**

In `photolib/actions/organize.py`, inside `move`, pass the two arguments to
`transfer_entry`:

```python
                skip_if_md5=(
                    row["match_md5"] if row["plan_verdict"] == "verify" else None
                ),
                adopt_id=(
                    row["plan_match"] if row["plan_verdict"] == "verify" else None
                ),
```

In the `else` branch of the result handling, guard `record_drive_file` and
adjust the message:

```python
            else:
                repo.mark_uploaded(
                    row["entry_id"], result.drive_file_id, result.md5
                )
                if result.adopted:
                    message = (
                        f"{row['name']}: already in Drive, verified by MD5 — "
                        "not uploaded."
                    )
                else:
                    # The Library browses drive_files; record the arrival so it
                    # is visible without waiting for the next Scan.
                    scans.record_drive_file(
                        drive_id=result.drive_file_id,
                        name=row["target_name"],
                        parent_path=row["target_folder"],
                        md5=result.md5,
                        size=result.size,
                        mime_type=mime_for(row["target_name"]),
                    )
                    message = f"{row['target_folder']}/{row['target_name']}"
                uploaded += 1
                level = "info"
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_transfer.py tests/test_action_organize.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/transfer.py photolib/actions/organize.py tests/test_transfer.py tests/test_action_organize.py tests/fakes/fake_drive.py
git commit -m "feat: prove identity by MD5 at transfer time and adopt the existing file"
```

---

### Task 12: The Sync from Archives flow

**Files:**
- Create: `photolib/actions/sync_archives.py`
- Test: `tests/test_action_sync_archives.py`

**Interfaces:**
- Consumes: `run_phase`/`phase_label` (Task 7), `JobItemsRepo` (Task 1), `ctx.run_id`/`ctx.cancelled` (Task 3), `MediaRepo.summary()["skipped"]` (Task 9).
- Produces: an action with `ID = "sync_archives"`, `GROUP = "flow"`, `ORDER = 1`, and `Params(confirm=False, run_id=None, limit=None, workers=4, retry_errors=False)`.

The flow's `job_items` are pure checkpoints under phase `"plan"`: one item
keyed `"planned"`, marked done once Plan finishes. Its purpose is to let the
confirm run tell "the plan you read" from "no plan at all". The Upload phase
enumerates nothing — `media.upload_status` is already its per-item ledger, and
duplicating it would create two sources of truth.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_sync_archives.py
import pytest

from photolib.actions import sync_archives
from photolib.db.job_items_repo import JobItemsRepo


def test_unconfirmed_run_stops_before_uploading(sync_context, conn):
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    messages = " ".join(e.message for e in events)
    assert "Re-run with confirm" in messages
    assert not any(e.phase and e.phase.startswith("Upload") for e in events)


def test_unconfirmed_run_records_that_a_plan_exists(sync_context, conn):
    list(sync_archives.run(sync_context, sync_archives.Params()))
    items = JobItemsRepo(conn).all(sync_context.run_id, "plan")
    assert [(i["item_key"], i["state"]) for i in items] == [("planned", "done")]


def test_confirmed_run_without_a_plan_refuses(sync_context, conn):
    events = list(sync_archives.run(
        sync_context, sync_archives.Params(confirm=True)
    ))
    assert events[-1].level == "error"
    assert "no plan" in events[-1].message.lower()


def test_confirmed_run_after_a_plan_reaches_the_upload_phase(
    sync_context, conn
):
    list(sync_archives.run(sync_context, sync_archives.Params()))
    events = list(sync_archives.run(
        sync_context, sync_archives.Params(confirm=True)
    ))
    assert any(e.phase and e.phase.startswith("Upload") for e in events)


def test_progress_is_monotonic_and_bounded(sync_context):
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    values = [e.progress for e in events if e.progress is not None]
    assert values == sorted(values)
    assert 0.0 <= values[0] and values[-1] <= 1.0


def test_a_fatal_phase_error_ends_the_flow(sync_context_without_folders):
    events = list(sync_archives.run(
        sync_context_without_folders, sync_archives.Params()
    ))
    assert events[-1].level == "error"
    assert not any(e.phase and e.phase.startswith("Pair") for e in events)


def test_cancellation_between_phases_stops_the_flow(sync_context):
    sync_context.cancelled.set()
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    assert not any(e.phase and e.phase.startswith("Plan") for e in events)
```

Fixtures: `sync_context` builds an `ActionContext` over the `conn` fixture with
a `FakeDrive` holding a ZIP source folder with one small archive (build it with
`tests/fixtures/zipbuilder.py`) and an empty Global Photos folder, both
recorded in `SettingsRepo`; `run_id="run-test"`; `cancelled=threading.Event()`;
`writer` set to the same `FakeDrive`. `sync_context_without_folders` is the
same with no folders configured. Model both on the context construction in
`tests/test_action_organize.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_sync_archives.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.actions.sync_archives'`

- [ ] **Step 3: Write the flow**

```python
# photolib/actions/sync_archives.py
"""Get everything out of the archives and into the Global Photos folder.

Four cheap, read-only phases produce a plan; a fifth moves bytes. The gate
between them is `confirm`, and the plan survives in `media` and in
`job_items`, so confirming acts on exactly what was reported and a killed run
resumes instead of restarting.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions import (
    check_connection,
    organize,
    pair_metadata,
    plan_organize,
    scan_archives,
)
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.actions.phases import run_phase
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.media_repo import MediaRepo

ID = "sync_archives"
TITLE = "Sync from Archives"
DESCRIPTION = (
    "Extract every file from the ZIP archives into the Global Photos folder, "
    "skipping anything already there. Reports the plan and uploads nothing "
    "until you confirm."
)
ORDER = 1
GROUP = "flow"

PLAN_PHASE = "plan"
PLAN_ITEM = "planned"

_READ_ONLY = (
    ("Connect", (0.00, 0.02), check_connection),
    ("Scan", (0.02, 0.25), scan_archives),
    ("Pair", (0.25, 0.45), pair_metadata),
    ("Plan", (0.45, 0.55), plan_organize),
)
_TOTAL_PHASES = len(_READ_ONLY) + 1


class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None
    limit: int | None = None
    workers: int = 4
    retry_errors: bool = False


def _cancelled(ctx: ActionContext) -> bool:
    return ctx.cancelled is not None and ctx.cancelled.is_set()


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    items = JobItemsRepo(ctx.conn)
    run_id = ctx.run_id or "adhoc"

    if params.confirm and not items.all(run_id, PLAN_PHASE, "done"):
        yield ProgressEvent(
            "There is no plan for this run to confirm. Run Sync from Archives "
            "without confirm first, read what it reports, then confirm that "
            "run.",
            progress=1.0,
            level="error",
        )
        return

    for index, (name, span, module) in enumerate(_READ_ONLY, start=1):
        if _cancelled(ctx):
            return
        failed = False
        for event in run_phase(
            name, span, module.run, ctx, module.Params(),
            index=index, total=_TOTAL_PHASES,
        ):
            failed = failed or event.level == "error"
            yield event
        if failed:
            yield ProgressEvent(
                f"{name} failed; the flow stopped there. Fix the cause and "
                "resume this job.",
                progress=span[1],
                level="error",
            )
            return

    items.put(run_id, PLAN_PHASE, PLAN_ITEM, run_id, "done")

    summary = MediaRepo(ctx.conn).summary()
    yield ProgressEvent(
        f"{summary['pending']} file(s) to upload, {summary['skipped']} already "
        f"in the Global folder, {summary['errors']} in error. Open Review to "
        "see every file and where it would go.",
        progress=0.55,
    )

    if not params.confirm:
        yield ProgressEvent(
            "Nothing has been uploaded. Re-run with confirm to move the bytes.",
            progress=1.0,
        )
        return

    if _cancelled(ctx):
        return

    yield from run_phase(
        "Upload", (0.55, 1.00), organize.run, ctx,
        organize.Params(
            workers=params.workers,
            retry_errors=params.retry_errors,
            limit=params.limit,
        ),
        index=_TOTAL_PHASES, total=_TOTAL_PHASES,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action_sync_archives.py tests/test_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/sync_archives.py tests/test_action_sync_archives.py
git commit -m "feat: Sync from Archives flow"
```

---

### Task 13: Metadata enrichment for files with no sidecar

**Files:**
- Create: `photolib/enrich.py`
- Modify: `photolib/db/schema.sql`, `photolib/db/migrations.py`
- Modify: `photolib/db/scan_repo.py` (add `set_enrichment`, `unenriched`)
- Modify: `photolib/db/tags_repo.py` (add `ensure`)
- Test: `tests/test_enrich.py`, `tests/test_tags_repo.py`, `tests/test_scan_repo.py`

**Interfaces:**
- Consumes: `DriveFile.location()` and `app_properties` (Task 8).
- Produces:
  - `drive_files` gains `country TEXT`, `latitude REAL`, `longitude REAL`, `metadata_source TEXT`.
  - `photolib.enrich.Enrichment` dataclass: `capture_hint: int | None`, `latitude: float | None`, `longitude: float | None`, `country: str | None`, `metadata_source: str`, `tag_slugs: list[str]`.
  - `photolib.enrich.enrichment_for(file: DriveFile, geocoder) -> Enrichment`. `metadata_source` is `"exif"` when `imageMediaMetadata.time` supplied the date, `"file_time"` when `createdTime`/`modifiedTime` did, `"none"` when nothing did. `geocoder` may be `None` or disabled, in which case `country` is `None`.
  - `ScanRepo.set_enrichment(drive_id, *, capture_hint, latitude, longitude, country, metadata_source) -> None`.
  - `ScanRepo.unenriched() -> list[sqlite3.Row]` — live rows with `metadata_source IS NULL`.
  - `TagsRepo.ensure(slug: str) -> sqlite3.Row` — return the tag with this slug, creating it with `name = slug.replace("-", " ")` and the default colour if absent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_enrich.py
from photolib.drive.client import DriveFile
from photolib.enrich import enrichment_for


class _Geocoder:
    enabled = True

    def __init__(self, answer="Greece"):
        self.answer = answer
        self.calls = []

    def lookup(self, lat, lon):
        self.calls.append((lat, lon))
        return self.answer


def _file(**kwargs) -> DriveFile:
    base = {"id": "d1", "name": "a.heic", "mimeType": "image/heic"}
    return DriveFile(**{**base, **kwargs})


def test_exif_time_is_the_preferred_date():
    file = _file(
        imageMediaMetadata={"time": "2025:07:14 10:30:00"},
        createdTime="2026-01-01T00:00:00Z",
    )
    result = enrichment_for(file, None)
    assert result.metadata_source == "exif"
    assert result.capture_hint == 1752489000


def test_file_time_is_the_fallback():
    file = _file(createdTime="2026-01-01T00:00:00Z")
    result = enrichment_for(file, None)
    assert result.metadata_source == "file_time"
    assert result.capture_hint is not None


def test_no_date_at_all():
    result = enrichment_for(_file(), None)
    assert result.metadata_source == "none"
    assert result.capture_hint is None


def test_gps_becomes_a_country():
    geocoder = _Geocoder()
    file = _file(imageMediaMetadata={"location": {"latitude": 37.9,
                                                  "longitude": 23.7}})
    result = enrichment_for(file, geocoder)
    assert (result.latitude, result.longitude) == (37.9, 23.7)
    assert result.country == "Greece"
    assert geocoder.calls == [(37.9, 23.7)]


def test_no_gps_never_calls_the_geocoder():
    geocoder = _Geocoder()
    enrichment_for(_file(), geocoder)
    assert geocoder.calls == []


def test_tag_properties_become_slugs():
    file = _file(appProperties={"t_family": "1", "t_greece-2025": "1",
                                "source_crc": "123"})
    result = enrichment_for(file, None)
    assert sorted(result.tag_slugs) == ["family", "greece-2025"]


def test_no_tag_properties_is_an_empty_list():
    assert enrichment_for(_file(appProperties={"country": "GR"}), None).tag_slugs == []
```

Append to `tests/test_tags_repo.py`:

```python
def test_ensure_creates_a_missing_tag_from_its_slug(conn):
    repo = TagsRepo(conn)
    tag = repo.ensure("greece-2025")
    assert tag["slug"] == "greece-2025"
    assert tag["name"] == "greece 2025"


def test_ensure_returns_the_existing_tag(conn):
    repo = TagsRepo(conn)
    created = repo.create("Family")
    assert repo.ensure(created["slug"])["id"] == created["id"]
```

Append to `tests/test_scan_repo.py`:

```python
def test_set_enrichment_and_unenriched(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files([
        {"drive_id": "d1", "name": "a.heic", "parent_path": "2025-01",
         "md5": "x", "size": 3, "mime_type": "image/heic", "capture_hint": None},
    ])
    assert [r["drive_id"] for r in repo.unenriched()] == ["d1"]
    repo.set_enrichment("d1", capture_hint=1700000000, latitude=37.9,
                        longitude=23.7, country="Greece",
                        metadata_source="exif")
    assert repo.unenriched() == []
    row = conn.execute(
        "SELECT * FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert (row["country"], row["metadata_source"]) == ("Greece", "exif")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.enrich'`

- [ ] **Step 3: Add the columns and repo methods**

In `photolib/db/schema.sql`, the `drive_files` table gains:

```sql
    country         TEXT,
    latitude        REAL,
    longitude       REAL,
    metadata_source TEXT
```

In `photolib/db/migrations.py`, extend `_ADDED_COLUMNS`:

```python
    ("drive_files", "country", "country TEXT"),
    ("drive_files", "latitude", "latitude REAL"),
    ("drive_files", "longitude", "longitude REAL"),
    ("drive_files", "metadata_source", "metadata_source TEXT"),
```

In `photolib/db/scan_repo.py`:

```python
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
```

In `photolib/db/tags_repo.py`:

```python
    def ensure(self, slug: str) -> sqlite3.Row:
        """The tag with this slug, created from it when absent.

        Enrich uses this to bring a `t_*` appProperty back into the catalog
        after a rebuild, so Drive is the durable copy of a tag, not just a
        mirror of one.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tags WHERE slug = ?", (slug,)
            ).fetchone()
            if row is not None:
                return row
        return self.create(slug.replace("-", " "))
```

`create` slugifies its argument; `slugify("greece 2025")` must return
`"greece-2025"`. Check `photolib/db/tags_repo.py:33` and, if it does not
round-trip, pass the slug through explicitly rather than changing `slugify`.

- [ ] **Step 4: Write `enrich.py`**

```python
# photolib/enrich.py
"""What the catalog can learn about a file it did not upload.

Google gives Drive an EXIF capture time and, often, coordinates. Everything
else this app knows about a file arrived with the file, in a Takeout sidecar
that a manually-added file will never have. This module reads what Drive has
and nothing else — no filename guessing, no rules engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from photolib.drive.client import DriveFile

TAG_PREFIX = "t_"


@dataclass
class Enrichment:
    capture_hint: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None
    metadata_source: str = "none"
    """'exif' | 'file_time' | 'none' — which source supplied the date."""
    tag_slugs: list[str] = field(default_factory=list)


def _date_source(file: DriveFile) -> str:
    if (file.image_media_metadata or {}).get("time"):
        return "exif"
    return "file_time" if file.capture_hint() is not None else "none"


def enrichment_for(file: DriveFile, geocoder) -> Enrichment:
    """Everything Drive knows about `file`, resolved through `geocoder`."""
    coords = file.location()
    country = None
    if coords is not None and geocoder is not None and geocoder.enabled:
        country = geocoder.lookup(*coords)

    return Enrichment(
        capture_hint=file.capture_hint(),
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
        country=country,
        metadata_source=_date_source(file),
        tag_slugs=sorted(
            key[len(TAG_PREFIX):]
            for key in (file.app_properties or {})
            if key.startswith(TAG_PREFIX)
        ),
    )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_enrich.py tests/test_tags_repo.py tests/test_scan_repo.py tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add photolib/enrich.py photolib/db tests/test_enrich.py tests/test_tags_repo.py tests/test_scan_repo.py
git commit -m "feat: derive dates, coordinates, country and tags from Drive metadata"
```

---

### Task 14: The Library stops showing nulls for foreign files

**Files:**
- Modify: `photolib/db/library_repo.py` (`_SELECT`, `_where`, `facets`)
- Test: `tests/test_library_repo.py`

**Interfaces:**
- Consumes: `drive_files.country` and `capture_hint` (Task 13).
- Produces: `capture_time` and `country` in every Library row and facet come from `COALESCE(m.capture_time, d.capture_hint)` and `COALESCE(m.country, d.country)`. `ROW_FIELDS` is unchanged; the API contract does not move.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_library_repo.py`:

```python
def test_country_falls_back_to_the_drive_row(conn):
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, country, "
        " capture_hint, metadata_source) "
        "VALUES ('d1', 'a.heic', '2025-01', 'x', 3, 'image/heic', 'Greece', "
        " 1752489000, 'exif')"
    )
    conn.commit()
    row = LibraryRepo(conn).list_files(Filters(), 10, 0)["files"][0]
    assert row["country"] == "Greece"
    assert row["capture_time"] == 1752489000


def test_country_filter_matches_a_drive_only_file(conn):
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, country) "
        "VALUES ('d1', 'a.heic', '2025-01', 'x', 3, 'image/heic', 'Greece')"
    )
    conn.commit()
    assert LibraryRepo(conn).list_files(
        Filters(country="Greece"), 10, 0
    )["total"] == 1


def test_country_facet_counts_drive_only_files(conn):
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, country) "
        "VALUES ('d1', 'a.heic', '2025-01', 'x', 3, 'image/heic', 'Greece')"
    )
    conn.commit()
    countries = LibraryRepo(conn).facets()["countries"]
    assert {"value": "Greece", "count": 1} in countries
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_library_repo.py -k "falls_back or drive_only" -v`
Expected: FAIL — `assert None == 'Greece'`

- [ ] **Step 3: Coalesce in one place**

In `photolib/db/library_repo.py`, define the two expressions once, above
`_SELECT`, so the select, the filter and the facet cannot disagree:

```python
# Catalogued files know more than Drive does; files that arrived by another
# route know only what Enrich read off Drive. One expression each, used by the
# select, the WHERE clauses and the facets, so they can never diverge.
_CAPTURE = "COALESCE(m.capture_time, d.capture_hint)"
_COUNTRY = "COALESCE(m.country, d.country)"
```

Replace the two columns in `_SELECT`:

```python
    f"       {_CAPTURE} AS capture_time, m.capture_source, "
    f"       {_COUNTRY} AS country, "
```

In `_where`, replace every `m.country` with `_COUNTRY` and every
`m.capture_time` with `_CAPTURE`. In `facets`, the country grouping expression
becomes `_COUNTRY` and the month grouping keeps `d.parent_path` unchanged.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_library_repo.py tests/test_api_library.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/db/library_repo.py tests/test_library_repo.py
git commit -m "feat: Library reads dates and countries from Drive when the catalog has none"
```

---

### Task 15: Split planning from execution in dedupe and repack

**Files:**
- Create: `photolib/dedupe.py`, `photolib/repack.py`
- Modify: `photolib/actions/clear_duplicates.py`, `photolib/actions/reorganize.py`
- Test: `tests/test_dedupe.py`, `tests/test_repack.py`

This task changes **no behaviour**. `tests/test_action_clear_dupes.py` and
`tests/test_action_reorganize.py` must pass untouched afterwards; that is the
proof the extraction is faithful.

**Interfaces:**
- Consumes: `photolib.scan.index_destination` (Task 8) is *not* used here; these planners read `drive_files` and Drive directly, exactly as the actions do today.
- Produces:
  - `photolib.dedupe.Removal` dataclass: `drive_id: str`, `name: str`, `parent_path: str`, `md5: str`, `keeper_id: str`, `keeper_path: str`.
  - `photolib.dedupe.plan_removals(drive, conn, root_id: str) -> tuple[list[Removal], list[str]]` — the removals, and the drive ids of zero-byte files reported but left alone.
  - `photolib.dedupe.apply_removal(writer, removal: Removal, conn) -> None` — trashes the file and stamps `drive_files.trashed_at`.
  - `photolib.repack.Move` dataclass: `drive_id: str`, `name: str`, `new_name: str`, `from_path: str`, `to_folder: str`.
  - `photolib.repack.plan_moves(drive, conn, root_id: str, *, exclude: set[str] = frozenset()) -> list[Move]` — `exclude` drops files that dedupe is about to trash, so the packing does not reserve space for them.
  - `photolib.repack.apply_move(writer, conn, move: Move, folder_ids: dict[str, str]) -> None`.
  - `photolib.repack.ensure_folders(writer, root_id: str, folders: list[str]) -> dict[str, str]`.
  - `photolib.repack.plan_sweep(drive, root_id: str) -> list[tuple[str, str]]` — `(folder_id, name)` of folders now empty.
  - `photolib.repack.apply_sweep(writer, folder_id: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dedupe.py
from photolib.dedupe import plan_removals
from tests.fakes.fake_drive import FakeDrive


def _library() -> FakeDrive:
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("m1", "2025-01", parent="root")
    drive.add_file("a", "one.heic", b"same", parent="m1")
    drive.add_file("b", "one-copy.heic", b"same", parent="m1")
    drive.add_file("c", "other.heic", b"different", parent="m1")
    return drive


def test_one_copy_of_each_group_survives(conn):
    removals, _ = plan_removals(_library(), conn, "root")
    assert [r.drive_id for r in removals] == ["b"]
    assert removals[0].keeper_id == "a"


def _verified_upload(conn, drive_id: str) -> None:
    """Record `drive_id` as a copy this pipeline uploaded and verified."""
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z', 'z.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries "
        "(archive_id, path, name, crc32, size, compressed_size, method, "
        " local_header_offset, kind) "
        "VALUES (1, 'Takeout/one.heic', 'one.heic', 111, 4, 4, 8, 0, 'media')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, upload_status, drive_file_id, md5) "
        "VALUES (1, 'done', ?, 'x')",
        (drive_id,),
    )
    conn.commit()


def test_a_verified_upload_is_preferred_as_the_keeper(conn):
    _verified_upload(conn, "b")
    removals, _ = plan_removals(_library(), conn, "root")
    assert [r.drive_id for r in removals] == ["a"]
    assert removals[0].keeper_id == "b"


def test_zero_byte_files_are_reported_not_removed(conn):
    drive = _library()
    drive.add_file("z1", "empty1.heic", b"", parent="m1")
    drive.add_file("z2", "empty2.heic", b"", parent="m1")
    removals, zero = plan_removals(drive, conn, "root")
    assert set(zero) == {"z1", "z2"}
    assert "z1" not in {r.drive_id for r in removals}
```

```python
# tests/test_repack.py
from datetime import datetime, timezone

from photolib.repack import plan_moves, plan_sweep
from tests.fakes.fake_drive import FakeDrive


def _epoch(month: str) -> int:
    return int(
        datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc).timestamp()
    )


def _seed(conn, files: list[tuple[str, str, str]]) -> None:
    """files: (drive_id, parent_path, capture month like '2025-01')."""
    conn.executemany(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, capture_hint) "
        "VALUES (?, ?, ?, ?, 4, 'image/heic', ?)",
        [
            (drive_id, f"{drive_id}.heic", parent, drive_id, _epoch(month))
            for drive_id, parent, month in files
        ],
    )
    conn.commit()


def test_a_file_already_in_its_bucket_produces_no_move(conn):
    _seed(conn, [("d1", "2025-01", "2025-01")])
    assert plan_moves(FakeDrive(), conn, "root") == []


def test_a_file_in_the_wrong_folder_is_moved(conn):
    _seed(conn, [("d1", "back_2019", "2025-01")])
    moves = plan_moves(FakeDrive(), conn, "root")
    assert [(m.drive_id, m.from_path, m.to_folder) for m in moves] == [
        ("d1", "back_2019", "2025-01")
    ]


def test_excluded_files_do_not_reserve_bucket_space(conn):
    """A file about to be trashed must not push a month into its own bucket."""
    _seed(conn, [(f"d{i}", "back_2019", "2025-01") for i in range(150)])
    with_all = {m.to_folder for m in plan_moves(FakeDrive(), conn, "root")}
    doomed = {f"d{i}" for i in range(100)}
    without = {
        m.to_folder
        for m in plan_moves(FakeDrive(), conn, "root", exclude=doomed)
    }
    # 150 files in one month stands alone; 50 merges with its neighbours.
    assert with_all == {"2025-01"}
    assert without != with_all


def test_a_name_collision_in_the_destination_is_renamed(conn):
    conn.executemany(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, capture_hint) "
        "VALUES (?, 'IMG_1.heic', ?, ?, 4, 'image/heic', ?)",
        [
            ("d1", "back_2019", "aaa", _epoch("2025-01")),
            ("d2", "back_2020", "bbb", _epoch("2025-01")),
        ],
    )
    conn.commit()
    names = {m.new_name for m in plan_moves(FakeDrive(), conn, "root")}
    assert len(names) == 2, "two files cannot land on one name"


def test_sweep_lists_only_empty_folders(conn):
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("empty", "back_2019", parent="root")
    drive.add_folder("full", "2025-01", parent="root")
    drive.add_file("f", "a.heic", b"a", parent="full")
    assert [name for _, name in plan_sweep(drive, "root")] == ["back_2019"]
```

If the exact bucket names these assertions expect differ from what
`photolib/buckets.py` produces for the seeded months, take the expected values
from `tests/test_buckets.py` rather than changing `buckets.py` — this task
changes no behaviour.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dedupe.py tests/test_repack.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.dedupe'`

- [ ] **Step 3: Extract the planners**

Move the grouping and keeper-selection logic out of
`photolib/actions/clear_duplicates.py` into `photolib/dedupe.py`, and the
bucket-diff, collision-rename and empty-folder logic out of
`photolib/actions/reorganize.py` into `photolib/repack.py`, matching the
signatures in the Interfaces block above. Both modules:

- take `drive`, `conn` and ids — never an `ActionContext`, so they are unit
  testable without one;
- return plain dataclasses describing intent, and expose separate `apply_*`
  functions that take a `writer`;
- keep the existing docstrings' reasoning with the code that implements it.

- [ ] **Step 4: Rewire the two actions onto them**

`clear_duplicates.run` becomes: call `plan_removals`, report every removal and
every zero-byte file, return unless `params.confirm`, then loop
`apply_removal`. `reorganize.run` becomes: `plan_moves` → report → return
unless confirmed → `ensure_folders` → loop `apply_move` → `plan_sweep` →
`apply_sweep`. The reported messages must not change; the existing action
tests assert on them.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS — in particular `tests/test_action_clear_dupes.py` and
`tests/test_action_reorganize.py` pass **unmodified**.

- [ ] **Step 6: Commit**

```bash
git add photolib/dedupe.py photolib/repack.py photolib/actions/clear_duplicates.py photolib/actions/reorganize.py tests/test_dedupe.py tests/test_repack.py
git commit -m "refactor: split duplicate and repack planning from their actions"
```

---

### Task 16: The Reorganize Folders flow

**Files:**
- Create: `photolib/actions/reorganize_library.py`
- Test: `tests/test_action_reorganize_library.py`

**Interfaces:**
- Consumes: `index_destination` (Task 8), `enrichment_for` (Task 13), `ScanRepo.set_enrichment`/`unenriched` and `TagsRepo.ensure` (Task 13), `dedupe`/`repack` planners (Task 15), `JobItemsRepo` (Task 1), `phase_label` (Task 7). It does **not** use `run_phase`: its phases are its own loops, not sub-actions, so it builds `ProgressEvent`s directly and only borrows the label format.
- Produces: an action with `ID = "reorganize_library"`, `GROUP = "flow"`, `ORDER = 2`, `Params(confirm=False, run_id=None)`.

Phase names and item keys, which the `/api/jobs/{id}/items` view exposes:

| Phase | `item_key` | `detail` |
| --- | --- | --- |
| `index` | folder drive id | `{"children": [folder ids]}` |
| `enrich` | file drive id | `{"source": "exif", "tags": ["family"]}` |
| `dedupe` | file drive id (the loser) | the `Removal` as a dict |
| `repack` | file drive id | the `Move` as a dict |
| `sweep` | folder drive id | `{"name": "back_2019"}` |

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_reorganize_library.py
from photolib.actions import reorganize_library
from photolib.db.job_items_repo import JobItemsRepo


def test_dry_run_changes_nothing_in_drive(reorg_context, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    assert drive.trashed == []
    assert drive.moves == []


def test_dry_run_persists_the_plan(reorg_context, conn):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    assert items.pending(reorg_context.run_id, "dedupe")
    assert items.pending(reorg_context.run_id, "repack")


def test_confirm_executes_exactly_the_persisted_plan(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    planned = {
        i["item_key"]
        for i in JobItemsRepo(conn).pending(reorg_context.run_id, "dedupe")
    }
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert set(drive.trashed) == planned


def test_confirm_without_a_plan_refuses(reorg_context, drive):
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert events[-1].level == "error"
    assert drive.trashed == []


def test_resume_does_not_repeat_finished_items(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    first = items.pending(reorg_context.run_id, "dedupe")[0]
    items.mark(reorg_context.run_id, "dedupe", first["item_key"], "done")
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert first["item_key"] not in drive.trashed


def test_dedupe_runs_before_repack(reorg_context, conn):
    """Files about to be trashed must not reserve space in a bucket."""
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params()
    ))
    phases = [e.phase for e in events if e.phase]
    assert phases.index(next(p for p in phases if p.startswith("Dedupe"))) < \
           phases.index(next(p for p in phases if p.startswith("Repack")))


def test_enrich_brings_drive_tags_into_the_catalog(reorg_context, conn):
    """A t_* appProperty on a file with no local tag creates that tag."""
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    slugs = {
        r["slug"] for r in conn.execute("SELECT slug FROM tags")
    }
    assert "family" in slugs
    linked = conn.execute(
        "SELECT COUNT(*) FROM file_tags"
    ).fetchone()[0]
    assert linked >= 1


def test_enrich_never_removes_a_local_tag(reorg_context, conn):
    from photolib.db.tags_repo import TagsRepo

    repo = TagsRepo(conn)
    tag = repo.create("local-only")
    repo.add_files(tag["id"], ["d-with-props"])
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM file_tags WHERE tag_id = ?", (tag["id"],)
    ).fetchone()[0]
    assert remaining == 1


def test_cancellation_stops_between_items(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    reorg_context.cancelled.set()
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert drive.trashed == []
```

`reorg_context` builds an `ActionContext` over a `FakeDrive` holding a Global
Photos folder with: two byte-identical files in different folders, one file
whose `appProperties` include `t_family`, and one empty folder. `run_id` is
fixed. `FakeDrive` needs a `moves` list recording `(file_id, new_parent)` in
its update method — add it if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_reorganize_library.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.actions.reorganize_library'`

- [ ] **Step 3: Write the flow**

```python
# photolib/actions/reorganize_library.py
"""Make the Global Photos folder tidy, whatever route its files took.

Five phases behind one confirm gate. The dry run writes every intended
operation into `job_items`; confirming reads those rows back and executes
exactly what was reported, marking each done as it goes. A killed confirm run
therefore resumes rather than re-planning, and the plan you approved is the
plan that runs.

Dedupe runs before Repack deliberately: a file about to be trashed must not
reserve space in a bucket, or every dedupe would leave the layout wrong.
"""

from __future__ import annotations

from typing import Iterator

from photolib import dedupe, enrich, places, repack, scan
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.actions.phases import phase_label
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.db.tags_repo import TagsRepo
from photolib.drive.errors import DriveError

ID = "reorganize_library"
TITLE = "Reorganize Folders"
DESCRIPTION = (
    "Index the Global Photos folder, fill in dates, countries and tags from "
    "Drive, remove duplicate copies, repack everything into ~100-file bucket "
    "folders and trash the folders left empty. Reports what it would do "
    "unless you confirm."
)
ORDER = 2
GROUP = "flow"

PHASES = ("index", "enrich", "dedupe", "repack", "sweep")
_LABELS = {
    "index": ("Index", (0.00, 0.20), 1),
    "enrich": ("Enrich", (0.20, 0.45), 2),
    "dedupe": ("Dedupe", (0.45, 0.60), 3),
    "repack": ("Repack", (0.60, 0.90), 4),
    "sweep": ("Sweep", (0.90, 1.00), 5),
}


class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None


def _label(phase: str) -> str:
    name, _, index = _LABELS[phase]
    return phase_label(name, index, len(PHASES))


def _progress(phase: str, done: int, total: int) -> float:
    _, (low, high), _ = _LABELS[phase]
    return low + (high - low) * (done / total if total else 1.0)


def _cancelled(ctx: ActionContext) -> bool:
    return ctx.cancelled is not None and ctx.cancelled.is_set()


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    items = JobItemsRepo(ctx.conn)
    scans = ScanRepo(ctx.conn)
    tags = TagsRepo(ctx.conn)
    run_id = ctx.run_id or "adhoc"

    root = ctx.settings.get_folder(PHOTOS_ROOT)
    if root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0, level="error",
        )
        return

    if params.confirm and not items.all(run_id, "dedupe") \
            and not items.all(run_id, "repack"):
        yield ProgressEvent(
            "There is no plan for this run to confirm. Run Reorganize Folders "
            "without confirm first, read what it reports, then confirm that "
            "run.",
            progress=1.0, level="error",
        )
        return

    # ---- Index -------------------------------------------------------
    done_folders = {
        row["item_key"]: (row["detail"] or {}).get("children", [])
        for row in items.all(run_id, "index", "done")
    }
    walked = 0

    def note(folder_id: str, children: list[str]) -> None:
        nonlocal walked
        walked += 1
        items.put(run_id, "index", folder_id, run_id, "done",
                  {"children": children})

    try:
        count = scan.index_destination(
            ctx.drive, ctx.conn, root.id, done=done_folders, on_folder=note
        )
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0, level="error",
        )
        return
    yield ProgressEvent(
        f"Indexed {count} file(s) across {walked} folder(s).",
        progress=_progress("index", 1, 1), phase=_label("index"),
        done=walked, total=walked,
    )
    if _cancelled(ctx):
        return

    # ---- Enrich ------------------------------------------------------
    pending = scans.unenriched()
    geocoder = places.Geocoder(
        ctx.conn, places.api_key_from_env(ctx.config.repo_root)
    )
    total = len(pending)
    for index, row in enumerate(pending, start=1):
        if _cancelled(ctx):
            return
        file = ctx.drive.get_file(row["drive_id"])
        result = enrich.enrichment_for(file, geocoder)
        scans.set_enrichment(
            row["drive_id"],
            capture_hint=result.capture_hint,
            latitude=result.latitude,
            longitude=result.longitude,
            country=result.country,
            metadata_source=result.metadata_source,
        )
        for slug in result.tag_slugs:
            tags.add_files(tags.ensure(slug)["id"], [row["drive_id"]])
        items.put(run_id, "enrich", row["drive_id"], run_id, "done",
                  {"source": result.metadata_source, "tags": result.tag_slugs})
        if index % 50 == 0 or index == total:
            yield ProgressEvent(
                f"Enriched {index} of {total}.",
                progress=_progress("enrich", index, total),
                phase=_label("enrich"), done=index, total=total,
            )
    if not total:
        yield ProgressEvent(
            "Nothing new to enrich.", progress=_progress("enrich", 1, 1),
            phase=_label("enrich"), done=0, total=0,
        )

    # ---- Dedupe (plan) -----------------------------------------------
    if not items.all(run_id, "dedupe"):
        removals, zero_byte = dedupe.plan_removals(ctx.drive, ctx.conn, root.id)
        for removal in removals:
            items.put(run_id, "dedupe", removal.drive_id, run_id, "pending",
                      removal.__dict__)
        for drive_id in zero_byte:
            items.put(run_id, "dedupe", drive_id, run_id, "skipped",
                      {"why": "zero-byte file, shares an MD5 with nothing"})
        yield ProgressEvent(
            f"{len(removals)} duplicate copy/copies to trash; "
            f"{len(zero_byte)} zero-byte file(s) left alone.",
            progress=_progress("dedupe", 0, 1), phase=_label("dedupe"),
        )

    doomed = {row["item_key"] for row in items.pending(run_id, "dedupe")}

    # ---- Repack (plan) -----------------------------------------------
    if not items.all(run_id, "repack"):
        moves = repack.plan_moves(ctx.drive, ctx.conn, root.id, exclude=doomed)
        for move in moves:
            items.put(run_id, "repack", move.drive_id, run_id, "pending",
                      move.__dict__)
        yield ProgressEvent(
            f"{len(moves)} file(s) to move into their bucket folder.",
            progress=_progress("repack", 0, 1), phase=_label("repack"),
        )

    if not params.confirm:
        yield ProgressEvent(
            "Nothing has changed in Drive. Re-run with confirm to apply this "
            "plan.",
            progress=1.0,
        )
        return

    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    # ---- Dedupe (apply) ----------------------------------------------
    outstanding = items.pending(run_id, "dedupe")
    for index, row in enumerate(outstanding, start=1):
        if _cancelled(ctx):
            return
        removal = dedupe.Removal(**row["detail"])
        try:
            dedupe.apply_removal(ctx.writer, removal, ctx.conn)
        except DriveError as exc:
            items.mark(run_id, "dedupe", row["item_key"], "failed",
                       {"error": str(exc)})
            yield ProgressEvent(f"{removal.name}: {exc}",
                                progress=_progress("dedupe", index,
                                                   len(outstanding)),
                                phase=_label("dedupe"), level="error",
                                done=index, total=len(outstanding))
            continue
        items.mark(run_id, "dedupe", row["item_key"], "done")
        yield ProgressEvent(
            f"Trashed {removal.parent_path}/{removal.name}",
            progress=_progress("dedupe", index, len(outstanding)),
            phase=_label("dedupe"), done=index, total=len(outstanding),
        )

    # ---- Repack (apply) ----------------------------------------------
    outstanding = items.pending(run_id, "repack")
    folder_ids = repack.ensure_folders(
        ctx.writer, root.id,
        sorted({row["detail"]["to_folder"] for row in outstanding}),
    )
    for index, row in enumerate(outstanding, start=1):
        if _cancelled(ctx):
            return
        move = repack.Move(**row["detail"])
        try:
            repack.apply_move(ctx.writer, ctx.conn, move, folder_ids)
        except DriveError as exc:
            items.mark(run_id, "repack", row["item_key"], "failed",
                       {"error": str(exc)})
            yield ProgressEvent(f"{move.name}: {exc}",
                                progress=_progress("repack", index,
                                                   len(outstanding)),
                                phase=_label("repack"), level="error",
                                done=index, total=len(outstanding))
            continue
        items.mark(run_id, "repack", row["item_key"], "done")
        yield ProgressEvent(
            f"{move.from_path} → {move.to_folder}/{move.new_name}",
            progress=_progress("repack", index, len(outstanding)),
            phase=_label("repack"), done=index, total=len(outstanding),
        )

    # ---- Sweep -------------------------------------------------------
    empty = repack.plan_sweep(ctx.drive, root.id)
    for index, (folder_id, name) in enumerate(empty, start=1):
        if _cancelled(ctx):
            return
        repack.apply_sweep(ctx.writer, folder_id)
        items.put(run_id, "sweep", folder_id, run_id, "done", {"name": name})
        yield ProgressEvent(
            f"Trashed the now-empty folder {name}.",
            progress=_progress("sweep", index, len(empty)),
            phase=_label("sweep"), done=index, total=len(empty),
        )

    yield ProgressEvent(
        f"Done. {len(items.all(run_id, 'dedupe', 'done'))} duplicate(s) "
        f"trashed, {len(items.all(run_id, 'repack', 'done'))} file(s) moved, "
        f"{len(empty)} folder(s) swept.",
        progress=1.0,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action_reorganize_library.py tests/test_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/reorganize_library.py tests/test_action_reorganize_library.py
git commit -m "feat: Reorganize Folders flow"
```

---

### Task 17: Verify Library

**Files:**
- Create: `photolib/actions/verify_library.py`
- Test: `tests/test_action_verify_library.py`

**Interfaces:**
- Consumes: `index_destination` (Task 8).
- Produces: an action with `ID = "verify_library"`, `ORDER = 90`, no `GROUP` (so it lands under Advanced), `Params()` empty. Writes nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_verify_library.py
from photolib.actions import verify_library


def _messages(ctx):
    return [e.message for e in verify_library.run(ctx, verify_library.Params())]


def test_reports_a_file_deleted_outside_the_app(verify_context, conn):
    conn.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'gone', "
        "md5 = 'x' WHERE id = 1"
    )
    conn.commit()
    assert any("no longer in Drive" in m for m in _messages(verify_context))


def test_reports_a_file_moved_outside_the_app(verify_context, conn):
    conn.execute("UPDATE drive_files SET parent_path = 'elsewhere'")
    conn.commit()
    assert any("moved" in m for m in _messages(verify_context))


def test_reports_an_md5_mismatch(verify_context, conn):
    conn.execute("UPDATE media SET md5 = 'not-what-drive-says'")
    conn.commit()
    assert any("MD5" in m for m in _messages(verify_context))


def test_reports_a_done_row_never_confirmed(verify_context, conn):
    conn.execute("UPDATE media SET upload_status = 'done', md5 = NULL")
    conn.commit()
    assert any("never confirmed" in m for m in _messages(verify_context))


def test_reports_orphan_tags(verify_context, conn):
    conn.execute("INSERT INTO tags (name, slug) VALUES ('x', 'x')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('ghost', 1)")
    conn.commit()
    assert any("no longer exist" in m for m in _messages(verify_context))


def test_writes_nothing(verify_context, conn, drive):
    before = conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0]
    _messages(verify_context)
    assert conn.execute(
        "SELECT COUNT(*) FROM drive_files"
    ).fetchone()[0] == before
    assert drive.trashed == []


def test_a_clean_library_reports_no_drift(clean_verify_context):
    assert any("No drift" in m for m in _messages(clean_verify_context))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_verify_library.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.actions.verify_library'`

- [ ] **Step 3: Write the action**

```python
# photolib/actions/verify_library.py
"""What the catalog believes, checked against what Drive actually holds.

Read-only and prescriptive of nothing. It answers one question — has anything
drifted since the last run? — and leaves the fixing to the flows.

It walks Drive live rather than trusting `drive_files`, because a stale index
is exactly one of the things it exists to find.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "verify_library"
TITLE = "Verify Library"
DESCRIPTION = (
    "Compare the catalog against what Drive actually holds and report every "
    "difference: files deleted or moved outside the app, MD5 mismatches, "
    "uploads never confirmed, and tags pointing at files that are gone. "
    "Writes nothing."
)
ORDER = 90

EXAMPLES = 20


class Params(ActionParams):
    pass


def _walk(drive, folder_id: str) -> dict[str, tuple[str, str | None]]:
    """Live files under `folder_id` as {drive_id: (parent_path, md5)}."""
    found: dict[str, tuple[str, str | None]] = {}
    stack = [(folder_id, "")]
    while stack:
        current, path = stack.pop()
        for child in drive.list_children(current):
            if child.is_folder:
                stack.append(
                    (child.id, f"{path}/{child.name}" if path else child.name)
                )
                continue
            found[child.id] = (path, child.md5)
    return found


def _report(label: str, rows: list[str], progress: float) -> ProgressEvent:
    head = ", ".join(rows[:EXAMPLES])
    more = f" (+{len(rows) - EXAMPLES} more)" if len(rows) > EXAMPLES else ""
    return ProgressEvent(
        f"{len(rows)} {label}: {head}{more}", progress=progress, level="warn"
    )


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    root = ctx.settings.get_folder(PHOTOS_ROOT)
    if root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0, level="error",
        )
        return

    try:
        live = _walk(ctx.drive, root.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0, level="error",
        )
        return

    yield ProgressEvent(f"Drive holds {len(live)} file(s).", progress=0.3)

    uploaded = list(ctx.conn.execute(
        "SELECT m.drive_file_id, m.md5, m.target_folder, m.target_name, e.name "
        "FROM media m JOIN entries e ON e.id = m.entry_id "
        "WHERE m.upload_status = 'done'"
    ))

    missing, moved, mismatched, unconfirmed = [], [], [], []
    for row in uploaded:
        if row["md5"] is None or row["drive_file_id"] is None:
            unconfirmed.append(row["name"])
            continue
        found = live.get(row["drive_file_id"])
        if found is None:
            missing.append(row["name"])
            continue
        parent_path, md5 = found
        if parent_path != row["target_folder"]:
            moved.append(f"{row['name']} → {parent_path}")
        if md5 is not None and md5 != row["md5"]:
            mismatched.append(row["name"])

    orphans = [
        r["drive_id"] for r in ctx.conn.execute(
            "SELECT DISTINCT ft.drive_id FROM file_tags ft "
            "LEFT JOIN drive_files d ON d.drive_id = ft.drive_id "
            "WHERE d.drive_id IS NULL"
        )
    ]

    categories = (
        ("file(s) recorded as uploaded are no longer in Drive", missing),
        ("file(s) were moved outside the app", moved),
        ("file(s) have an MD5 Drive disagrees with", mismatched),
        ("upload(s) are marked done but were never confirmed", unconfirmed),
        ("tagged file(s) no longer exist", orphans),
    )

    drift = False
    for index, (label, rows) in enumerate(categories, start=1):
        if not rows:
            continue
        drift = True
        yield _report(label, rows, 0.3 + 0.7 * index / len(categories))

    if not drift:
        yield ProgressEvent(
            f"No drift. {len(uploaded)} verified upload(s) all present, in "
            "place and matching.",
            progress=1.0,
        )
    else:
        yield ProgressEvent("Nothing was changed.", progress=1.0)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action_verify_library.py tests/test_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/verify_library.py tests/test_action_verify_library.py
git commit -m "feat: Verify Library reports catalog drift"
```

---

### Task 18: Progress, Cancel, Resume and verdicts in the UI

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`
- Modify: `web/src/components/JobProgress.tsx`, `web/src/pages/JobsPage.tsx`, `web/src/pages/ReviewPage.tsx`
- Test: `web/src/components/JobProgress.test.tsx` (new), `web/src/pages/JobsPage.test.tsx` (new), `web/src/pages/ReviewPage.test.tsx`

**Interfaces:**
- Consumes: `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/resume` (Tasks 4–5); `plan_verdict` on review rows (Task 9).
- Produces:
  - TS `Job` gains `run_id: string | null`, `resumed_from: string | null`, `phase: string | null`, `items_done: number`, `items_total: number`.
  - TS `ReviewMedia` gains `plan_verdict: string | null`, `plan_match: string | null`.
  - `client.ts` gains `cancelJob(id: string): Promise<Job>` and `resumeJob(id: string): Promise<Job>`.

- [ ] **Step 1: Write the failing tests**

```tsx
// web/src/components/JobProgress.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { JobProgress } from './JobProgress'

const job = {
  id: 'j1', action: 'sync_archives', params: {}, status: 'running' as const,
  progress: 0.5, message: 'uploading', error: null,
  created_at: '', started_at: null, finished_at: null,
  run_id: 'r1', resumed_from: null,
  phase: 'Upload (5/5)', items_done: 412, items_total: 842,
}

describe('JobProgress', () => {
  it('shows the phase and item counts', () => {
    render(<JobProgress job={job} />)
    expect(screen.getByText(/Upload \(5\/5\)/)).toBeInTheDocument()
    expect(screen.getByText(/412 \/ 842/)).toBeInTheDocument()
  })

  it('omits item counts when nothing was enumerated', () => {
    render(<JobProgress job={{ ...job, items_total: 0 }} />)
    expect(screen.queryByText(/\d+ \/ \d+/)).not.toBeInTheDocument()
  })
})
```

```tsx
// web/src/pages/JobsPage.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { JobsPage } from './JobsPage'
import * as client from '../api/client'

const base = {
  action: 'sync_archives', params: {}, progress: 0.5, message: null,
  error: null, created_at: '2026-08-12T10:00:00Z', started_at: null,
  finished_at: null, run_id: 'r1', resumed_from: null, phase: null,
  items_done: 0, items_total: 0,
}

describe('JobsPage', () => {
  beforeEach(() => {
    vi.spyOn(client, 'listJobs').mockResolvedValue([
      { ...base, id: 'running', status: 'running' },
      { ...base, id: 'failed', status: 'failed' },
      { ...base, id: 'done', status: 'done' },
    ])
  })

  it('offers Cancel only while a job can still be stopped', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/running/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(1)
  })

  it('offers Resume only for a failed or cancelled job', async () => {
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getAllByText(/failed/)).not.toHaveLength(0))
    expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1)
  })

  it('calls the API when Resume is clicked', async () => {
    const resume = vi.spyOn(client, 'resumeJob')
      .mockResolvedValue({ ...base, id: 'new', status: 'queued' })
    render(<MemoryRouter><JobsPage /></MemoryRouter>)
    const button = await screen.findByRole('button', { name: 'Resume' })
    button.click()
    await waitFor(() => expect(resume).toHaveBeenCalledWith('failed'))
  })
})
```

Append to `web/src/pages/ReviewPage.test.tsx`, following that file's existing
mock of `listReviewMedia`:

```tsx
it('shows the plan verdict for each file', async () => {
  vi.spyOn(client, 'listReviewMedia').mockResolvedValue({
    total: 3,
    files: [
      { ...reviewRow, entry_id: 1, name: 'a.heic', plan_verdict: 'skip' },
      { ...reviewRow, entry_id: 2, name: 'b.heic', plan_verdict: 'verify' },
      { ...reviewRow, entry_id: 3, name: 'c.heic', plan_verdict: 'upload' },
    ],
  })
  render(<MemoryRouter><ReviewPage /></MemoryRouter>)
  for (const verdict of ['skip', 'verify', 'upload']) {
    expect(await screen.findByText(verdict)).toBeInTheDocument()
  }
})
```

`reviewRow` is a complete `ReviewMedia` literal you add at the top of the file
if one is not already there; every other field may be `null`, `0` or `''`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — `Property 'phase' does not exist on type 'Job'`

- [ ] **Step 3: Extend the types and client**

In `web/src/api/types.ts`, `Job` gains:

```ts
  run_id: string | null
  resumed_from: string | null
  phase: string | null
  items_done: number
  items_total: number
```

and `ReviewMedia` gains:

```ts
  plan_verdict: string | null
  plan_match: string | null
```

In `web/src/api/client.ts`, following the existing helpers' shape:

```ts
export async function cancelJob(id: string): Promise<Job> {
  return post(`/api/jobs/${id}/cancel`, {})
}

export async function resumeJob(id: string): Promise<Job> {
  return post(`/api/jobs/${id}/resume`, {})
}
```

- [ ] **Step 4: Render them**

In `JobProgress.tsx`, add beneath the existing message line:

```tsx
{job.phase && (
  <p className="job-phase">
    {job.phase}
    {job.items_total > 0 && ` · ${job.items_done} / ${job.items_total}`}
  </p>
)}
```

In `JobsPage.tsx`, add a per-row control column:

```tsx
{(job.status === 'queued' || job.status === 'running') && (
  <button onClick={() => cancelJob(job.id).then(refresh)}>Cancel</button>
)}
{(job.status === 'failed' || job.status === 'cancelled') && (
  <button onClick={() => resumeJob(job.id).then(refresh)}>Resume</button>
)}
```

where `refresh` is the existing job-list reload in that page.

In `ReviewPage.tsx`, add a `Verdict` column rendering `row.plan_verdict ?? '—'`,
and add it to the page's existing filter control as a new option group.

The review API must return the field: in `photolib/api/routes_review.py`, add
`plan_verdict` and `plan_match` to the row dict it builds (the query already
selects `m.*` — confirm and extend if not).

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test && cd .. && uv run pytest tests/test_api_review.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web/src photolib/api/routes_review.py
git commit -m "feat: phase progress, cancel, resume and plan verdicts in the UI"
```

---

### Task 19: Document the two flows

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Rewrite "The pipeline" as "The two flows"**

Replace the eleven-row table with two sections — Sync from Archives and
Reorganize Folders — each listing its phases, what it writes, and the confirm
gate. Follow with an "Advanced" table listing the nine steps plus Verify
Library, prefaced by one sentence explaining they exist for re-running a single
phase and for recovery.

State plainly, replacing the current paragraph that says the opposite:

> Files whose bytes are already in the Global folder are **not** uploaded
> again. A file is skipped only on content evidence — an MD5 this app recorded,
> or one computed from the archive at transfer time. A file whose name matches
> but whose bytes differ is uploaded under a disambiguated name.

- [ ] **Step 2: Add a "Resuming and cancelling" section**

Cover: every phase checkpoints its work in `job_items`; Cancel stops on the
next item boundary and keeps those checkpoints; Resume starts a new job on the
same run and skips what is done; there is no automatic resume on restart, on
purpose.

- [ ] **Step 3: Update "Adding an action"**

Document the optional `GROUP` attribute (`"flow"` or `"advanced"`, defaulting
to `"advanced"`), and that `ORDER` sorts within a group.

- [ ] **Step 4: Update "Browsing and tagging"**

Note that dates, countries and tags for files that never came through an
archive are filled in by Reorganize from Drive's EXIF and `appProperties`, and
that `appProperties` are read back into the catalog — so losing the catalog no
longer loses your tags.

- [ ] **Step 5: Run the full suite one last time**

Run: `uv run pytest && cd web && npm test && cd ..`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: describe the two flows, resumability and the skip rule"
```

---

## Self-review notes

Spec coverage, section by section: §1 taxonomy → Task 6. §2 `phases.py` →
Task 7. §3 Sync flow → Task 12, resting on Tasks 8–11. §4 skip rule → Tasks
9–11. §5 checkpoints/resume/cancel → Tasks 1–5. §6 Reorganize flow → Task 16,
resting on Tasks 8, 13, 15. §7 Verify Library → Task 17. §8 migrations →
folded into Tasks 1, 2, 9, 13, each adding its own columns beside the code
that reads them. §9 API surface → Tasks 4, 5, 6. §10 testing → every task's
Step 1.

The three planning-time deviations are recorded at the top of this document
and were reported to the operator before the plan was written.

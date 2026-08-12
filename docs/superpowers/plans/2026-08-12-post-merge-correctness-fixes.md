# Post-Merge Correctness Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five correctness and safety gaps left open when the two-flows branch merged (`4d64b3e`), each of which was found by review, verified against the code, and deliberately deferred rather than dismissed.

**Architecture:** Five independent fixes, no shared design. The largest is thread-safety: SQLite access is serialised at the connection itself so no repo can forget, with explicit locks kept only where several statements must land as a unit. The rest are one-file corrections.

**Tech Stack:** Python 3.12, FastAPI, SQLite (`sqlite3` stdlib), pytest; React 18 + Vite + TypeScript + vitest.

## Global Constraints

- Python 3.12, managed by `uv`. Backend tests run with `uv run pytest`.
- The default backend suite **never touches the network.** Drive is faked by `tests/fakes/fake_drive.py`; ZIPs are built in memory by `tests/fixtures/zipbuilder.py`.
- New columns are added by `ALTER TABLE ADD COLUMN` in `migrations._ADDED_COLUMNS` **and** written into `schema.sql`, and the two paths must produce identical tables.
- Destructive Drive operations **trash**, never delete.
- Frontend tests: `cd web && npm test`. Type check: `cd web && npx tsc -b`.
- **Baseline that must not regress: backend 618 passed, 14 deselected; frontend 156 passed.**
- Never run `git stash` or any stash subcommand — the stash stack is shared with other checkouts. Use a temporary WIP commit instead.

## Context: why these five

The two-flows branch shipped after 19 reviewed tasks plus a whole-branch review. These are what that review and its predecessors raised, confirmed as real, and left. Everything cosmetic — stale test names, weak assertions, docstring drift — is **out of scope** and recorded in "Not in scope" at the end.

## File Structure

| File | Change |
| --- | --- |
| `tests/test_migrations.py` | Strip the branch's 11 columns so the upgrade path is actually exercised |
| `photolib/db/catalog.py` | Serialise `execute`/`executemany`/`executescript`/`commit` on the connection |
| `photolib/db/media_repo.py`, `tags_repo.py`, `scan_repo.py`, `library_repo.py` | Explicit locks for multi-statement units and lazily-iterated cursors |
| `photolib/api/routes_review.py` | Add `entry_id` to `ROW_FIELDS` |
| `photolib/actions/plan_organize.py` | Stop reserving destination names for `done` rows |
| `photolib/actions/organize.py` | Emit item counts so Sync's progress line renders |

---

### Task 1: Make the migration parity test actually exercise the upgrade path

**Files:**
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks. This is a test-only change.

**Why this matters.** `test_upgrading_a_v2_catalog_matches_a_fresh_one` is the sole enforcement of the rule that `schema.sql` and `migrations._ADDED_COLUMNS` produce identical tables. Its assertion is generic and *would* catch drift — `_schema()` enumerates `sqlite_master` and `PRAGMA table_info`. But it builds its "old catalog" by dropping a hardcoded list, `V3_COLUMNS + V4_COLUMNS`, which predates the two-flows branch. `_ADDED_COLUMNS` now has 19 entries; only 8 are stripped. The other **11 are never dropped**, so both sides of the comparison get them from `schema.sql`, the `ALTER TABLE` path never runs, and a typo in any of those 11 definitions would pass every test in the suite.

The definitions were hand-verified to match during the final review, so nothing is broken today. This closes the hole before the next column is added.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrations.py`:

```python
def test_the_v2_simulation_really_removes_the_later_columns(tmp_path):
    """Guards the guard.

    `test_upgrading_a_v2_catalog_matches_a_fresh_one` only proves anything if
    its simulated old catalog is genuinely missing the columns the migration
    adds. If the stripping list ever falls behind `_ADDED_COLUMNS` again, both
    sides get the columns from schema.sql and the comparison passes without
    exercising a single ALTER TABLE.
    """
    old = tmp_path / "old.db"
    conn = catalog.connect(old)
    for table, column in V3_COLUMNS + V4_COLUMNS + V6_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DROP TABLE job_items")

    for table, column in V3_COLUMNS + V4_COLUMNS + V6_COLUMNS:
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert column not in present, f"{table}.{column} survived the strip"

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "job_items" not in tables
    conn.close()


def test_every_added_column_is_covered_by_the_strip_list():
    """A column in _ADDED_COLUMNS that no test strips is a column whose
    ALTER TABLE path is never run."""
    stripped = set(V3_COLUMNS + V4_COLUMNS + V6_COLUMNS)
    declared = {(table, column) for table, column, _ in migrations._ADDED_COLUMNS}
    assert declared - stripped == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL — `NameError: name 'V6_COLUMNS' is not defined`

- [ ] **Step 3: Add the stripping list and use it**

In `tests/test_migrations.py`, after `V4_COLUMNS`:

```python
# Everything the two-flows branch added at schema version 6. Stripping these
# is what makes the upgrade path actually run its ALTER TABLE statements —
# without it, both sides of the parity comparison get the columns from
# schema.sql and the test passes without proving anything.
V6_COLUMNS = (
    ("jobs", "run_id"),
    ("jobs", "resumed_from"),
    ("jobs", "phase"),
    ("jobs", "items_done"),
    ("jobs", "items_total"),
    ("media", "plan_verdict"),
    ("media", "plan_match"),
    ("drive_files", "country"),
    ("drive_files", "latitude"),
    ("drive_files", "longitude"),
    ("drive_files", "metadata_source"),
)
```

In `test_upgrading_a_v2_catalog_matches_a_fresh_one`, extend the strip loop and drop the table the branch introduced:

```python
    for table, column in V3_COLUMNS + V4_COLUMNS + V6_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DROP TABLE job_items")
    conn.execute("DROP TABLE file_tags")
    conn.execute("DROP TABLE tags")
    conn.execute("PRAGMA user_version = 2")
```

Add `from photolib.db import migrations` to the imports if it is not already there — the coverage test reads `migrations._ADDED_COLUMNS`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_migrations.py tests/test_catalog_v2.py -v`
Expected: PASS. If the parity test now **fails**, that is the point of the exercise — a real mismatch between `schema.sql` and `_ADDED_COLUMNS` has surfaced. Fix the definition, do not weaken the test, and say what you found in your report.

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest`
Expected: 620 passed, 14 deselected (618 + 2 new).

```bash
git add tests/test_migrations.py
git commit -m "test: exercise the ALTER TABLE path for the columns the branch added"
```

---

### Task 2: Serialise SQLite access at the connection

**Files:**
- Modify: `photolib/db/catalog.py`
- Modify: `photolib/db/scan_repo.py`, `photolib/db/tags_repo.py`, `photolib/db/library_repo.py`
- Test: `tests/test_catalog.py`, `tests/test_scan_repo.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `LockedConnection.execute`/`executemany`/`executescript`/`commit` acquire `self.lock` for the duration of the call. `conn.lock` remains a public reentrant lock any repo may hold across several statements.

**Why this matters.** `catalog.connect` opens the database with `check_same_thread=False`, and one connection object is shared between the job-runner thread and FastAPI's request threads. `LockedConnection`'s own docstring says the lock exists precisely because per-repo locks cannot serialise a shared connection. Yet:

| Repo | Lock attribute | Methods holding it |
| --- | --- | --- |
| `JobsRepo` | yes | all |
| `JobItemsRepo` | yes | all |
| `SettingsRepo` | yes | all |
| `ScanRepo` | yes | 3 of 13 — only those added by the branch |
| `MediaRepo` | **no** | none, across 17 statements |
| `TagsRepo` | **no** | none, across 13 statements |
| `LibraryRepo` | **no** | none |

Adding `with self._lock:` to ~40 methods by hand is easy to get wrong and starts over the moment someone writes a new repo. Serialising in `LockedConnection` cannot be forgotten and covers every repo at once, including future ones.

**The limitation, stated plainly:** wrapping `execute` protects the *statement*, not a lazily-iterated cursor. `conn.execute("SELECT …")` returns a cursor that fetches rows as you iterate; if another thread uses the connection mid-iteration, the wrapper has already released the lock. So Step 4 also materialises the handful of sites that iterate a cursor lazily. Every other read in the codebase already calls `list(...)`, `.fetchone()` or `.fetchall()` inside the statement call.

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_locking.py`:

```python
import threading

from photolib.db import catalog


def test_concurrent_writes_on_one_connection_do_not_interleave(tmp_path):
    """The connection is shared between the job runner and request threads.

    Without serialisation this raises or loses rows; with it, every insert
    lands exactly once.
    """
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE counter (n INTEGER)")

    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def hammer(base: int) -> None:
        barrier.wait()
        try:
            for i in range(50):
                conn.execute("INSERT INTO counter (n) VALUES (?)", (base + i,))
        except Exception as exc:  # noqa: BLE001 - the assertion is the report
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(b * 1000,)) for b in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert conn.execute("SELECT COUNT(*) FROM counter").fetchone()[0] == 200
    conn.close()


def test_execute_holds_the_connection_lock(tmp_path):
    """Pins the mechanism, not just the outcome: a statement must not run
    while another thread holds conn.lock."""
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (n INTEGER)")

    ran = threading.Event()

    def insert() -> None:
        conn.execute("INSERT INTO t (n) VALUES (1)")
        ran.set()

    with conn.lock:
        worker = threading.Thread(target=insert)
        worker.start()
        assert not ran.wait(timeout=0.2), "execute ran while the lock was held"

    worker.join(timeout=5.0)
    assert ran.is_set()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_catalog_locking.py -v`
Expected: `test_execute_holds_the_connection_lock` FAILS with "execute ran while the lock was held". The first test may pass by luck — SQLite serialises individual statements internally — which is exactly why the second test exists.

- [ ] **Step 3: Serialise on the connection**

In `photolib/db/catalog.py`, extend `LockedConnection`:

```python
class LockedConnection(sqlite3.Connection):
    """A Connection carrying one RLock that every repo over it shares.

    Per-repository locks cannot provide mutual exclusion for a single
    physical connection used by multiple repo instances (JobsRepo,
    SettingsRepo, ...): two different lock objects guarding the same
    connection give no protection against each other. Attaching the lock
    to the connection itself, instead, means every repo that shares the
    connection shares the same lock.

    Every statement issued through this connection takes that lock, so a
    repo cannot forget to. What the wrapper cannot do is hold the lock
    while a caller iterates a cursor lazily — `execute` returns once the
    statement is prepared, and rows are fetched afterwards. Reads that
    iterate rather than materialise must therefore take `conn.lock`
    themselves, as must any sequence of statements that has to land as a
    unit.

    Attempting to set an arbitrary attribute on a plain sqlite3.Connection
    raises AttributeError, so the lock is defined on this subclass instead.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()

    def execute(self, *args, **kwargs):
        with self.lock:
            return super().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self.lock:
            return super().executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self.lock:
            return super().executescript(*args, **kwargs)

    def commit(self):
        with self.lock:
            return super().commit()
```

The lock is an `RLock`, so a repo method that already holds it and then issues statements does not deadlock.

- [ ] **Step 4: Materialise the lazily-iterated reads**

Four sites iterate a cursor rather than materialising it. Two already hold the lock; two do not. Wrap the unguarded pair so the rows are fetched while the lock is held.

`photolib/db/scan_repo.py:161` (`drive_file_names`) and `photolib/db/tags_repo.py:188` (`slugs_by_file`) — give `TagsRepo` the shared lock in `__init__` first:

```python
        self._lock = conn.lock
```

then wrap each method's body in `with self._lock:` so the `for row in self._conn.execute(...)` loop runs inside it.

`photolib/db/library_repo.py:124,139` — `LibraryRepo` has no lock; add `self._lock = conn.lock` in `__init__` and wrap the two loops the same way.

Verify no site is left: `grep -rn "for row in self._conn.execute" photolib/db/` and account for each hit.

- [ ] **Step 5: Lock the multi-statement units**

Serialising each statement does not make a *sequence* atomic. These issue several statements that must land together; give each an explicit `with self._lock:` around the whole body:

- `ScanRepo.upsert_drive_files` — `executemany` then a full-table `DELETE` sweep keyed to the same timestamp. A read between them sees an index that is missing rows.
- `ScanRepo.replace_entries` — delete-then-insert.
- `TagsRepo.merge` — re-points `file_tags` then deletes the source tag.

Audit for others: `grep -n "def " photolib/db/*.py` and check any method issuing more than one `execute`. Name in your report every method you added a lock to and every multi-statement method you judged safe without one.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_catalog_locking.py tests/test_catalog.py tests/test_scan_repo.py tests/test_tags_repo.py tests/test_library_repo.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest`
Expected: 622 passed, 14 deselected.

Watch for a **hang** rather than a failure: a deadlock shows up as the suite stopping, not erroring. If that happens, the cause is almost certainly a non-reentrant lock or a lock held across a blocking call — report it rather than removing locks until it clears.

```bash
git add photolib/db tests/test_catalog_locking.py
git commit -m "fix: serialise SQLite access on the shared connection"
```

---

### Task 3: Fix the Review page's Retry button

**Files:**
- Modify: `photolib/api/routes_review.py:12-17`
- Test: `tests/test_api_review.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GET /api/review/media` rows gain `entry_id`.

**Why this matters.** `ReviewPage.tsx:146` calls `onRetry(row.entry_id)` and posts to `POST /api/review/retry/{entry_id}`, which FastAPI types as `int`. But `ROW_FIELDS` in `routes_review.py` does not include `entry_id`, so `row.entry_id` is `undefined`, the request goes to `/api/review/retry/undefined`, and FastAPI rejects it with 422. The button has never worked. The frontend test supplies `entry_id` in its own mock, which is why nothing caught it.

The TypeScript `ReviewMedia` interface already declares `entry_id: number` — only the server's field list is wrong.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_review.py`:

```python
def test_media_rows_carry_the_entry_id_the_retry_route_needs(client):
    """The Review page's Retry button posts row.entry_id to
    /api/review/retry/{entry_id}. Without the field it posts `undefined`."""
    rows = client.get("/api/review/media").json()["rows"]
    assert rows, "fixture must produce at least one media row"
    assert all(isinstance(row["entry_id"], int) for row in rows)


def test_retry_accepts_an_id_taken_from_a_media_row(client):
    row = client.get("/api/review/media").json()["rows"][0]
    assert client.post(f"/api/review/retry/{row['entry_id']}").status_code == 200
```

Follow whatever client/fixture idiom `tests/test_api_review.py` already uses; if its fixture produces no media rows, seed one the way the file's other tests do.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_review.py -k entry_id -v`
Expected: FAIL — `KeyError: 'entry_id'`

- [ ] **Step 3: Add the field**

In `photolib/api/routes_review.py`:

```python
ROW_FIELDS = (
    "entry_id",
    "name", "path", "archive_name", "target_folder", "target_name",
    "capture_time", "capture_source", "country",
    "duplicate_of", "duplicate_reason", "upload_status",
    "error", "drive_file_id", "plan_verdict", "plan_match",
)
```

`MediaRepo._MEDIA_SELECT` starts `SELECT m.*, …`, so `entry_id` is already in
every row and no query change is needed. Confirm that rather than taking it on
trust.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_api_review.py -v && cd web && npm test && cd ..`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add photolib/api/routes_review.py tests/test_api_review.py
git commit -m "fix: Review rows carry entry_id, so Retry stops posting undefined"
```

---

### Task 4: Stop reserving destination names for finished rows

**Files:**
- Modify: `photolib/actions/plan_organize.py:164-172`
- Test: `tests/test_action_plan.py`

**Interfaces:**
- Consumes: `media_repo._DONE_PROTECTS` behaviour from the merged branch — `set_plan` will not overwrite `target_folder`/`target_name` for a row whose `upload_status` is `done`.
- Produces: nothing consumed elsewhere.

**Why this matters.** The merged branch made `set_plan` refuse to overwrite `target_folder`/`target_name` for a `done` row, because for a finished file those columns record where the file *is* rather than where it should go. But `plan_organize` still computes a destination for every row and adds it to the `taken` collision set:

```python
        if verdict == "verify" or (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        taken.add((folder, name))
```

For a `done` row the computed `(folder, name)` is discarded by the repo — yet it still occupies a slot in `taken`. A genuinely pending file that legitimately wants that name is then renamed with a CRC suffix to avoid a collision that does not exist. The result is a correct but needlessly ugly filename, permanently.

**Fix:** only reserve a slot for rows whose target will actually be written — that is, rows that are not `done`. Keep the `verify` disambiguation exactly as it is; a `verify` row is pending and does still reserve.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_action_plan.py`:

```python
def test_a_done_row_does_not_reserve_its_computed_name(conn, ctx):
    """A finished file's computed destination is discarded by set_plan, so it
    must not push a pending file into a disambiguated name."""
    from photolib.actions import plan_organize
    from photolib.db.media_repo import MediaRepo

    repo = MediaRepo(conn)
    rows = repo.all_media()
    assert len(rows) >= 2, "fixture must provide two entries sharing a name"

    # The first entry is already uploaded; the second is still pending and
    # shares its name, so both compute the same destination.
    repo.mark_uploaded(rows[0]["entry_id"], "drive-done", "abc123")

    list(plan_organize.run(ctx, plan_organize.Params()))

    pending = [r for r in repo.all_media() if r["upload_status"] == "pending"]
    assert pending, "the second entry should still be pending"
    assert "~" not in pending[0]["target_name"], (
        "a done row's discarded destination must not force a rename"
    )
```

**Fixtures — these names are verified against the file, do not rename them.**
`tests/test_action_plan.py` defines `ctx` (line 39) and `planned_catalog`
(line 64, "One archive, two entries; a live Drive file matches the first by
name+size"). Use `ctx`. If its archive's two entries do **not** share a
filename, the collision this test needs cannot arise — extend the fixture or
add a sibling that seeds two same-named entries, and say which you did.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_action_plan.py -k done_row_does_not_reserve -v`
Expected: FAIL — the pending row's `target_name` carries a `~xxxxxx` suffix.

- [ ] **Step 3: Reserve only what will be written**

In `photolib/actions/plan_organize.py`, replace the block:

```python
        month = buckets.month_of(capture)
        folder = fmap[month] if month else buckets.UNKNOWN_FOLDER
        name = row["name"]
        # A `verify` row's destination name is already occupied by the file it
        # matched. If the MD5s disagree at transfer time this uploads, so it
        # must upload under a free name; if they agree, the name is unused.
        if verdict == "verify" or (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        # Only rows whose target will actually be written may claim a slot.
        # `set_plan` discards target_folder/target_name for a `done` row —
        # its columns record where the file already is — so reserving one
        # here would rename a pending file to dodge a collision that never
        # exists.
        if row["upload_status"] != "done":
            taken.add((folder, name))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action_plan.py tests/test_action_organize.py tests/test_api_review.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and commit**

Run: `uv run pytest`
Expected: 623 passed, 14 deselected.

```bash
git add photolib/actions/plan_organize.py tests/test_action_plan.py
git commit -m "fix: a finished row no longer reserves a destination name it never uses"
```

---

### Task 5: Sync from Archives reports item counts

**Files:**
- Modify: `photolib/actions/organize.py`
- Test: `tests/test_action_organize.py`, `tests/test_action_sync_archives.py`

**Interfaces:**
- Consumes: `ProgressEvent(message, progress, level, phase, done, total)` and `run_phase`, both unchanged.
- Produces: Organize's per-file events carry `done` and `total`.

**Why this matters.** `JobProgress` renders `Upload (5/5) · 412 / 842` when a job row has `items_total > 0`. Reorganize populates those counts; **no phase of Sync from Archives does**, so the item line never appears for the flow that runs longest and needs it most. `grep -n "done=\|total=" photolib/actions/*.py` returns only `sync_archives`'s `index=`/`total=` arguments to `run_phase`, which are phase positions, not item counts.

Upload is the phase worth fixing: it is the one that moves 36 GB. The read-only phases finish in seconds.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_action_organize.py`:

```python
def test_upload_events_carry_item_counts(ctx):
    """JobProgress renders `412 / 842` only when the event supplies them."""
    from photolib.actions import organize

    events = [
        e for e in organize.run(ctx, organize.Params())
        if e.total is not None
    ]
    assert events, "no upload event carried item counts"
    assert events[-1].done == events[-1].total
    assert all(e.done <= e.total for e in events)
```

And to `tests/test_action_sync_archives.py`:

```python
def test_the_upload_phase_reports_item_counts(sync_context):
    """The counts must survive run_phase's rescaling into the flow."""
    from photolib.actions import sync_archives

    list(sync_archives.run(sync_context, sync_archives.Params()))
    events = list(sync_archives.run(
        sync_context, sync_archives.Params(confirm=True)
    ))
    counted = [
        e for e in events
        if e.phase and e.phase.startswith("Upload") and e.total is not None
    ]
    assert counted, "the Upload phase reported no item counts"
```

**Fixtures — verified names.** `tests/test_action_organize.py` defines `ctx`
(line 56) and `archive_content` (line 46); it has no `organize_context`.
`tests/test_action_sync_archives.py` defines `sync_context` (line 20) and
`sync_context_without_folders` (line 44). Reuse these rather than adding new
ones.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_organize.py -k item_counts tests/test_action_sync_archives.py -k item_counts -v`
Expected: FAIL — `assert [] , "no upload event carried item counts"`

- [ ] **Step 3: Emit the counts**

In `photolib/actions/organize.py`, the `as_completed` loop already tracks `uploaded` and `failed` and knows `len(rows)`. Add the counts to the per-file event it yields:

```python
            done_bytes += row["size"]
            yield ProgressEvent(
                message,
                progress=min(done_bytes / total_bytes, 1.0),
                level=level,
                done=uploaded + failed,
                total=len(rows),
            )
```

`run_phase` copies `done`/`total` through unchanged — confirm that in `photolib/actions/phases.py` rather than assuming it.

Leave the opening "Uploading N file(s)" event alone; it is a summary, not an item.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_action_organize.py tests/test_action_sync_archives.py tests/test_jobs_runner.py -v`
Expected: PASS

- [ ] **Step 5: Run both suites and commit**

Run: `uv run pytest && cd web && npm test && cd ..`
Expected: backend 625 passed, 14 deselected; frontend 156 passed.

```bash
git add photolib/actions/organize.py tests/test_action_organize.py tests/test_action_sync_archives.py
git commit -m "fix: Upload reports item counts, so Sync's progress line renders"
```

---

## Not in scope

Recorded so they are not lost, deliberately excluded from this plan:

**Test hygiene.** `test_version_is_five` and `test_schema_version_is_five` assert 6. `test_midpoint_lands_in_the_middle_of_the_span` uses span `(0.0, 1.0)`, a no-op rescale. `run_phase`'s clamp has no test. `test_the_index_is_written_in_one_sweep` is weak alone. `scan.py`'s `seen` cycle guard is untested. No both-sides-NULL country test in `library_repo`.

**Duplication and naming.** `verified_by_crc` duplicates `uploaded_by_name`'s query. `job_items_repo`'s `put`/`mark` repeat a state guard; `put` lacks a docstring for its detail-merge semantics. `Nav.test.tsx` keeps a redundant cast. `planned_catalog` builds its context unlike its neighbours.

**Documentation drift.** `library_repo.py:7-8` says a foreign file "shows nulls" — no longer true. `ReviewPage.tsx` still says duplicates "will still be uploaded — deduplication is a separate, later step", describing the behaviour the branch reversed, and no tile surfaces `summary.skipped`. `ReviewPage.tsx:109` says "a filtered count may be incomplete" even with no filter active. The README's checkpointing paragraph omits Sweep. `cancel()`'s docstring overstates the queued branch.

**Accepted by design.** The two TOCTOU windows documented inline in `runner.py` — cancel may return `True` for a job that finishes in the window, and a queued job may run to its first yield. `set_enrichment` overwrites country/coordinates unconditionally, safe while `unenriched()` gates on `metadata_source IS NULL`. `SCHEMA_VERSION` left at 6 across two column additions; `migrate()` never reads it for control flow. Skip evidence is `(crc32, size)` — 32 bits plus a size, with a small but non-zero residual collision risk the design accepted knowingly.

**Larger, needs its own design.** `sync_tags` still issues one `appProperties` GET per candidate. Caching them was considered and rejected during the merge: `sync_tags` is reachable standalone with no enforced preceding Index, so a stored snapshot could be arbitrarily stale and would break its documented promise to read Drive for the diff. Making it safe needs a freshness policy, not a cache.

## Self-review notes

Each task was verified against merged master (`4d64b3e`) before being written: the 19 entries in `_ADDED_COLUMNS` against the 8 stripped by the v2 simulation; the per-repo lock counts in the table above by grep; `ROW_FIELDS` against `ReviewPage.tsx:146` and the `retry` route's `int` parameter; the `taken.add` placement in `plan_organize`; and the absence of any `done=`/`total=` in the Sync phases' modules.

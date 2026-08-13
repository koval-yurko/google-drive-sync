# Plan/Execution Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `photolib/` so that deciding what should happen and doing it live in separate packages, and so that SQL lives only in `db/`.

**Architecture:** Two new packages, `photolib/planning/` (takes readers, returns a value) and `photolib/execution/` (takes a writer, returns `None`). The eleven loose top-level modules move into those packages, into the subsystem packages they adapt (`drive/`, `ziparchive/`), or stay top-level with a name that says what they are. Raw SQL in eight modules outside `db/` moves behind repository methods, two of them on new repos. An `import-linter` contract in CI keeps the layering true.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, sqlite3, pytest, uv, hatchling.

## Global Constraints

- Python >= 3.12. Every module starts with `from __future__ import annotations`.
- Run tests with `uv run pytest`. Live tests are deselected by default via `addopts = "-m 'not live'"` — never remove that.
- The connection is `catalog.LockedConnection` with `isolation_level = None` (autocommit). Every repo takes `conn.lock` as `self._lock`.
- **Lock rule:** `execute` releases the lock once the statement is prepared, so rows fetched afterwards are unprotected. Any read that iterates or materialises a cursor, and any sequence of statements that must land as a unit, must hold `with self._lock:` for the whole operation. This is documented in `photolib/db/catalog.py`.
- Repos take a `sqlite3.Connection` and expose methods; they never receive a `drive` or `writer` object.
- `photolib/db/` must not import from `photolib/planning/`, `photolib/execution/`, `photolib/drive/`, or `photolib/ziparchive/`.
- Commit after every task. Use `git mv` for moves so history follows the file.
- Do not reformat or restructure code you are only moving. A move commit should show as a rename wherever possible.

---

## File Structure

**New files:**
- `photolib/db/layout_repo.py` — cross-table reads/writes for the library's folder layout
- `photolib/db/geocache_repo.py` — the geocoding cache table
- `photolib/planning/__init__.py`, `takeout.py`, `buckets.py`, `layout.py`, `duplicates.py`, `enrich.py`
- `photolib/execution/__init__.py`, `transfer.py`, `moves.py`, `trash.py`, `downloads.py`
- `photolib/drive/thumbs.py`, `photolib/ziparchive/source.py`, `photolib/ingest.py`
- `tests/test_layout_repo.py`, `tests/test_geocache_repo.py`
- `tests/test_planning_layout.py`, `tests/test_execution_moves.py`
- `tests/test_planning_duplicates.py`, `tests/test_execution_trash.py`

**Deleted after their contents move:** `photolib/repack.py`, `photolib/dedupe.py`, `photolib/buckets.py`, `photolib/takeout.py`, `photolib/enrich.py`, `photolib/transfer.py`, `photolib/downloads.py`, `photolib/thumbs.py`, `photolib/archives.py`, `photolib/scan.py`, `tests/test_repack.py`, `tests/test_dedupe.py`.

**Stays top-level:** `photolib/config.py`, `photolib/main.py`, `photolib/places.py`.

---

### Task 1: LayoutRepo

The layout questions span `drive_files` (ScanRepo's table) and `media` (MediaRepo's), so they belong to neither. This repo is named for its consumer, `photolib.planning.layout`.

`month_of` cannot be imported here — that would be a `db → planning` edge. The month is computed in SQL instead: `strftime('%Y-%m', capture_hint, 'unixepoch')` is exactly equivalent to `datetime.fromtimestamp(capture, tz=timezone.utc).strftime("%Y-%m")`, and rows with a NULL capture are filtered in SQL rather than skipped in Python.

`exclude` is filtered in Python, not with a SQL `NOT IN`, because the set is unbounded and SQLite caps bound parameters.

**Files:**
- Create: `photolib/db/layout_repo.py`
- Test: `tests/test_layout_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `LayoutRepo(conn: sqlite3.Connection)`
  - `.unaccounted_months(exclude: set[str] = frozenset()) -> Counter[str]`
  - `.capture_histogram(exclude: set[str] = frozenset()) -> Counter[str]`
  - `.live_files_for_layout(exclude: set[str] = frozenset()) -> list[sqlite3.Row]` — rows carry `drive_id, name, parent_path, md5, media_id, capture`
  - `.record_move(drive_id: str, folder: str, name: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout_repo.py`:

```python
from collections import Counter

from photolib.db.layout_repo import LayoutRepo


def _seed(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind) VALUES"
        " (1, 'p/IMG_1.HEIC', 'IMG_1.HEIC', 1, 1, 1, 8, 0, 'media')"
    )
    # A catalogued file: its month comes from media.capture_time.
    conn.execute(
        "INSERT INTO media (entry_id, capture_time, drive_file_id)"
        " VALUES (1, 1704067200, 'd1')"          # 2024-01
    )
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d1', 'IMG_1.HEIC', '2024-01', 1500000000)"
    )
    # An unaccounted legacy file: only its hint dates it.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d2', 'IMG_2.HEIC', 'back_2024_01', 1704153600)"   # 2024-01
    )
    # A trashed file counts for nothing.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint,"
        " trashed_at) VALUES ('d3', 'IMG_3.HEIC', 'x', 1704240000, 'now')"
    )
    # An undated legacy file is skipped by the histograms.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d4', 'IMG_4.HEIC', 'back_2024_01')"
    )
    conn.commit()


def test_unaccounted_months_skips_catalogued_trashed_and_undated(conn):
    _seed(conn)
    assert LayoutRepo(conn).unaccounted_months() == Counter({"2024-01": 1})


def test_capture_histogram_counts_media_and_unaccounted_files(conn):
    _seed(conn)
    assert LayoutRepo(conn).capture_histogram() == Counter({"2024-01": 2})


def test_capture_histogram_drops_excluded_ids_from_both_halves(conn):
    _seed(conn)
    repo = LayoutRepo(conn)
    assert repo.capture_histogram(exclude={"d1"}) == Counter({"2024-01": 1})
    assert repo.capture_histogram(exclude={"d1", "d2"}) == Counter()


def test_month_computed_in_sql_matches_utc(conn):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d9', 'x.HEIC', 'p', 1700000000)"      # 2023-11 in UTC
    )
    conn.commit()
    assert LayoutRepo(conn).unaccounted_months() == Counter({"2023-11": 1})


def test_live_files_for_layout_returns_capture_and_skips_excluded(conn):
    _seed(conn)
    rows = LayoutRepo(conn).live_files_for_layout()
    by_id = {row["drive_id"]: row for row in rows}
    assert set(by_id) == {"d1", "d2", "d4"}
    # A catalogued file dates from media.capture_time, not the Drive hint.
    assert by_id["d1"]["capture"] == 1704067200
    assert by_id["d2"]["capture"] == 1704153600
    assert by_id["d1"]["parent_path"] == "2024-01"

    kept = LayoutRepo(conn).live_files_for_layout(exclude={"d2"})
    assert {row["drive_id"] for row in kept} == {"d1", "d4"}


def test_record_move_updates_both_tables_together(conn):
    _seed(conn)
    LayoutRepo(conn).record_move("d1", "2024-01 - 2024-03", "IMG_1~ab12.HEIC")

    drive_row = conn.execute(
        "SELECT parent_path, name FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert drive_row["parent_path"] == "2024-01 - 2024-03"
    assert drive_row["name"] == "IMG_1~ab12.HEIC"

    media_row = conn.execute(
        "SELECT target_folder, target_name FROM media WHERE drive_file_id = 'd1'"
    ).fetchone()
    assert media_row["target_folder"] == "2024-01 - 2024-03"
    assert media_row["target_name"] == "IMG_1~ab12.HEIC"


def test_record_move_rolls_back_when_the_second_update_fails(conn):
    """Both tables agree or neither changes. Today's two-statement version
    can leave drive_files moved and media not."""
    _seed(conn)
    repo = LayoutRepo(conn)

    real_execute = conn.execute
    calls = []

    def failing_execute(sql, *args, **kwargs):
        calls.append(sql)
        if sql.startswith("UPDATE media"):
            raise sqlite3.OperationalError("boom")
        return real_execute(sql, *args, **kwargs)

    conn.execute = failing_execute
    try:
        with pytest.raises(sqlite3.OperationalError):
            repo.record_move("d1", "moved", "moved.HEIC")
    finally:
        conn.execute = real_execute

    unchanged = conn.execute(
        "SELECT parent_path FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert unchanged["parent_path"] == "2024-01"
```

Add these two imports at the top of the file, above the `from collections` line:

```python
import sqlite3

import pytest
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_layout_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.db.layout_repo'`

- [ ] **Step 3: Write the implementation**

Create `photolib/db/layout_repo.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_layout_repo.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Nothing calls `LayoutRepo` yet, so this must be green.

- [ ] **Step 6: Commit**

```bash
git add photolib/db/layout_repo.py tests/test_layout_repo.py
git commit -m "Add LayoutRepo for the cross-table layout queries

Months are computed in SQL so the persistence layer never imports
planning.buckets.month_of. record_move puts the drive_files and media
updates in one transaction, which the two loose statements in
repack.apply_move are not."
```

---

### Task 2: GeocacheRepo, and a Geocoder that does not know SQL

**Files:**
- Create: `photolib/db/geocache_repo.py`
- Modify: `photolib/places.py:48-100`
- Modify: `photolib/actions/steps/plan_organize.py:104-106`
- Modify: `photolib/actions/reorganize_library.py:126-128`
- Test: `tests/test_geocache_repo.py`, `tests/test_places.py:19`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `GeocacheRepo(conn: sqlite3.Connection)`
  - `.get(key: str) -> str | None | _Missing` — see note below
  - `.put(key: str, country: str | None, payload: dict) -> None`
  - `Geocoder(cache: GeocacheRepo, api_key: str | None, http=None)` — first parameter changes from `conn`

**A cached `None` is not a cache miss.** `geocache` stores `country = NULL` for coordinates the API resolved to no country, and today's code distinguishes that from "not cached" by checking whether the *row* is None. `.get()` preserves this by returning a sentinel for a miss.

- [ ] **Step 1: Write the failing test**

Create `tests/test_geocache_repo.py`:

```python
from photolib.db.geocache_repo import MISSING, GeocacheRepo


def test_get_returns_missing_for_an_unknown_key(conn):
    assert GeocacheRepo(conn).get("50.00,30.00") is MISSING


def test_put_then_get_round_trips_the_country(conn):
    repo = GeocacheRepo(conn)
    repo.put("50.00,30.00", "Ukraine", {"results": []})
    assert repo.get("50.00,30.00") == "Ukraine"


def test_a_cached_none_is_a_hit_not_a_miss(conn):
    """The API answering 'no country here' must not be re-requested."""
    repo = GeocacheRepo(conn)
    repo.put("0.00,0.00", None, {"results": []})
    assert repo.get("0.00,0.00") is None
    assert repo.get("0.00,0.00") is not MISSING


def test_put_overwrites_an_existing_key(conn):
    repo = GeocacheRepo(conn)
    repo.put("50.00,30.00", "Ukraine", {"v": 1})
    repo.put("50.00,30.00", "Poland", {"v": 2})
    assert repo.get("50.00,30.00") == "Poland"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_geocache_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'photolib.db.geocache_repo'`

- [ ] **Step 3: Write the repo**

Create `photolib/db/geocache_repo.py`:

```python
"""The reverse-geocoding cache.

A cached country of `None` means the API answered and found no country
there — a real result worth not re-requesting. `MISSING` is what a key
that was never looked up returns, so the two stay distinguishable.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Final


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


class GeocacheRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def get(self, key: str) -> str | None | _Missing:
        row = self._conn.execute(
            "SELECT country FROM geocache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return MISSING
        return row["country"]

    def put(self, key: str, country: str | None, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO geocache (key, country, raw_json) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET country = excluded.country, "
                "raw_json = excluded.raw_json",
                (key, country, json.dumps(payload)),
            )
            self._conn.commit()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_geocache_repo.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Point Geocoder at the repo**

In `photolib/places.py`, replace the `sqlite3` import with nothing (it becomes unused) and rewrite `__init__`, `lookup` and `_store`. Delete the `import sqlite3` line and add `from photolib.db.geocache_repo import MISSING, GeocacheRepo`:

```python
class Geocoder:
    def __init__(
        self, cache: GeocacheRepo, api_key: str | None, http=None
    ) -> None:
        self._cache = cache
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def lookup(self, lat: float, lon: float) -> str | None:
        """The country at these coordinates, or None."""
        key = cache_key(lat, lon)
        cached = self._cache.get(key)
        if cached is not MISSING:
            return cached

        if not self._api_key:
            return None

        try:
            response = self._http.get(
                GEOCODE_URL,
                params={"latlng": f"{lat},{lon}", "key": self._api_key},
            )
            if not response.is_success:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        country = self._extract(payload)
        self._cache.put(key, country, payload)
        return country
```

Delete the `_store` method entirely. Leave `_extract`, `cache_key`, `api_key_from_env`, `GEOCODE_URL` and `ENV_VAR` untouched. Also delete the now-unused `import json`.

- [ ] **Step 6: Update the three construction sites**

`photolib/actions/steps/plan_organize.py:104` and `photolib/actions/reorganize_library.py:126` both read:

```python
    geocoder = places.Geocoder(
        ctx.conn, places.api_key_from_env(ctx.config.repo_root)
    )
```

Change both to:

```python
    geocoder = places.Geocoder(
        GeocacheRepo(ctx.conn), places.api_key_from_env(ctx.config.repo_root)
    )
```

Add `from photolib.db.geocache_repo import GeocacheRepo` to the imports of both files.

In `tests/test_places.py:19`, change the fixture to build the repo:

```python
    return Geocoder(
        GeocacheRepo(conn),
```

and add `from photolib.db.geocache_repo import GeocacheRepo` to its imports. Leave the rest of the fixture's arguments as they are.

- [ ] **Step 7: Run the affected tests**

Run: `uv run pytest tests/test_places.py tests/test_geocache_repo.py tests/test_action_plan.py tests/test_action_reorganize_library.py -v`
Expected: PASS.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add photolib/db/geocache_repo.py photolib/places.py \
        photolib/actions/steps/plan_organize.py \
        photolib/actions/reorganize_library.py \
        tests/test_geocache_repo.py tests/test_places.py
git commit -m "Move the geocache behind GeocacheRepo

Geocoder now takes the repo rather than a connection, so it is testable
without SQLite. A cached NULL country stays distinguishable from a cache
miss via the MISSING sentinel."
```

---

### Task 3: MediaRepo query methods

**Files:**
- Modify: `photolib/db/media_repo.py` (add methods to `MediaRepo`)
- Test: `tests/test_media_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all on `MediaRepo`:
  - `.uploaded_drive_ids() -> set[str]`
  - `.uploaded_with_names() -> list[sqlite3.Row]` — rows carry `drive_file_id, md5, target_folder, target_name, name`
  - `.sidecar(sidecar_id: int) -> sqlite3.Row | None`
  - `.exists(entry_id: int) -> bool`
  - `.review_page(*, folder: str | None, duplicates_only: bool, limit: int, offset: int) -> dict` — `{"total": int, "rows": list[sqlite3.Row]}`

- [ ] **Step 1: Write the failing tests**

Append this to `tests/test_media_repo.py`. The seed helper is self-contained, so it does not matter what that file already defines — give it a distinct name to avoid clashing.

```python
def _seed_uploads(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    for n in (1, 2, 3):
        conn.execute(
            "INSERT INTO entries (archive_id, path, name, crc32, size,"
            " compressed_size, method, local_header_offset, kind) VALUES"
            f" (1, 'p/IMG_{n}.HEIC', 'IMG_{n}.HEIC', {n}, 1, 1, 8, 0, 'media')"
        )
    conn.execute(
        "INSERT INTO media (entry_id, upload_status, drive_file_id, md5,"
        " target_folder, target_name) VALUES"
        " (1, 'done', 'd1', 'abc', '2024-01', 'IMG_1.HEIC')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, upload_status, drive_file_id, md5,"
        " target_folder, target_name, duplicate_of) VALUES"
        " (2, 'done', 'd2', 'def', '2024-02', 'IMG_2.HEIC', 1)"
    )
    # Never uploaded: no drive_file_id, and not 'done'.
    conn.execute(
        "INSERT INTO media (entry_id, upload_status) VALUES (3, 'pending')"
    )
    conn.commit()


def test_uploaded_drive_ids_skips_rows_with_no_drive_file(conn):
    _seed_uploads(conn)
    assert MediaRepo(conn).uploaded_drive_ids() == {"d1", "d2"}


def test_uploaded_with_names_returns_done_rows_ordered_by_entry_name(conn):
    _seed_uploads(conn)
    rows = MediaRepo(conn).uploaded_with_names()
    assert [row["name"] for row in rows] == ["IMG_1.HEIC", "IMG_2.HEIC"]
    assert rows[0]["drive_file_id"] == "d1"
    assert rows[0]["md5"] == "abc"
    assert rows[0]["target_folder"] == "2024-01"


def test_exists_reports_whether_an_entry_has_a_media_row(conn):
    _seed_uploads(conn)
    assert MediaRepo(conn).exists(1) is True
    assert MediaRepo(conn).exists(999) is False


def test_sidecar_returns_the_row_or_none(conn):
    _seed_uploads(conn)
    repo = MediaRepo(conn)
    assert repo.sidecar(999) is None
    sidecar_id = repo.save_sidecar(1, {"title": "t"}, "{}")
    assert repo.sidecar(sidecar_id)["title"] == "t"


def test_review_page_counts_and_pages(conn):
    _seed_uploads(conn)
    page = MediaRepo(conn).review_page(
        folder=None, duplicates_only=False, limit=1, offset=0
    )
    assert page["total"] == 3
    assert len(page["rows"]) == 1


def test_review_page_filters_by_folder_and_duplicates(conn):
    _seed_uploads(conn)
    repo = MediaRepo(conn)

    by_folder = repo.review_page(
        folder="2024-02", duplicates_only=False, limit=50, offset=0
    )
    assert by_folder["total"] == 1
    assert by_folder["rows"][0]["target_name"] == "IMG_2.HEIC"

    dupes = repo.review_page(
        folder=None, duplicates_only=True, limit=50, offset=0
    )
    assert dupes["total"] == 1
    assert dupes["rows"][0]["duplicate_of"] == 1


def test_review_page_rows_carry_the_entry_and_archive_columns(conn):
    """routes_review projects these; the row must still have them."""
    _seed_uploads(conn)
    row = MediaRepo(conn).review_page(
        folder="2024-01", duplicates_only=False, limit=1, offset=0
    )["rows"][0]
    assert row["name"] == "IMG_1.HEIC"
    assert row["path"] == "p/IMG_1.HEIC"
    assert row["archive_name"] == "a.zip"
    assert row["entry_size"] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_media_repo.py -v -k "uploaded_drive_ids or uploaded_with_names or exists or sidecar_returns or review_page"`
Expected: FAIL — `AttributeError: 'MediaRepo' object has no attribute ...`

- [ ] **Step 3: Add the methods**

Append to `MediaRepo` in `photolib/db/media_repo.py`, after `verified_by_crc`:

```python
    # ---------- queries for actions and the Review page ----------

    def uploaded_drive_ids(self) -> set[str]:
        """Drive ids this pipeline uploaded and recorded."""
        with self._lock:
            return {
                row[0]
                for row in self._conn.execute(
                    "SELECT drive_file_id FROM media "
                    "WHERE drive_file_id IS NOT NULL"
                )
            }

    def uploaded_with_names(self) -> list[sqlite3.Row]:
        """Every row recorded as uploaded, with the entry name that
        identifies it in a report."""
        with self._lock:
            return list(
                self._conn.execute(
                    "SELECT m.drive_file_id, m.md5, m.target_folder, "
                    "       m.target_name, e.name "
                    "FROM media m JOIN entries e ON e.id = m.entry_id "
                    "WHERE m.upload_status = 'done' "
                    "ORDER BY e.name"
                )
            )

    def sidecar(self, sidecar_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM sidecars WHERE id = ?", (sidecar_id,)
        ).fetchone()

    def exists(self, entry_id: int) -> bool:
        row = self._conn.execute(
            "SELECT id FROM media WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return row is not None

    def review_page(
        self,
        *,
        folder: str | None,
        duplicates_only: bool,
        limit: int,
        offset: int,
    ) -> dict:
        """One page of the Review table, with the unpaged total beside it.

        Count and page are taken under one lock so the total cannot describe
        a different state of the catalog than the rows do.
        """
        where, args = [], []
        if folder:
            where.append("m.target_folder = ?")
            args.append(folder)
        if duplicates_only:
            where.append("m.duplicate_of IS NOT NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM media m {clause}", args
            ).fetchone()[0]
            rows = list(
                self._conn.execute(
                    "SELECT m.*, e.path, e.name, e.size AS entry_size, "
                    "       a.name AS archive_name "
                    "FROM media m "
                    "JOIN entries e ON e.id = m.entry_id "
                    "JOIN archives a ON a.id = e.archive_id "
                    f"{clause} "
                    "ORDER BY m.target_folder, m.target_name "
                    "LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            )
        return {"total": total, "rows": rows}
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_media_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add photolib/db/media_repo.py tests/test_media_repo.py
git commit -m "Add MediaRepo methods for the queries actions and routes inline

review_page takes its count and its page under one lock, which the two
loose statements in routes_review do not."
```

---

### Task 4: ScanRepo query methods

**Files:**
- Modify: `photolib/db/scan_repo.py` (add methods to `ScanRepo`)
- Test: `tests/test_scan_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, on `ScanRepo`:
  - `.mark_trashed(drive_id: str, when: str) -> None`
  - `.archive_modified_time(drive_id: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scan_repo.py`:

```python
def test_mark_trashed_stamps_the_row(conn):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d1', 'IMG_1.HEIC', 'p')"
    )
    conn.commit()

    ScanRepo(conn).mark_trashed("d1", "2026-08-13T00:00:00+00:00")

    row = conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert row["trashed_at"] == "2026-08-13T00:00:00+00:00"


def test_mark_trashed_is_safe_to_replay(conn):
    """A resumed run can trash the same file twice; the second stamp just
    overwrites the first."""
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d1', 'IMG_1.HEIC', 'p')"
    )
    conn.commit()

    repo = ScanRepo(conn)
    repo.mark_trashed("d1", "first")
    repo.mark_trashed("d1", "second")

    row = conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert row["trashed_at"] == "second"


def test_archive_modified_time_returns_the_value_or_none(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size, modified_time)"
        " VALUES ('z1', 'a.zip', 1, '2024-01-01T00:00:00Z')"
    )
    conn.commit()

    repo = ScanRepo(conn)
    assert repo.archive_modified_time("z1") == "2024-01-01T00:00:00Z"
    assert repo.archive_modified_time("nope") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_scan_repo.py -v -k "mark_trashed or archive_modified_time"`
Expected: FAIL — `AttributeError: 'ScanRepo' object has no attribute 'mark_trashed'`

- [ ] **Step 3: Add the methods**

Append to `ScanRepo` in `photolib/db/scan_repo.py`, before the `# ---------- reporting ----------` comment:

```python
    def mark_trashed(self, drive_id: str, when: str) -> None:
        """Stamp a file as trashed. Safe to replay: an overwrite of a field
        with a value it may already hold."""
        with self._lock:
            self._conn.execute(
                "UPDATE drive_files SET trashed_at = ? WHERE drive_id = ?",
                (when, drive_id),
            )
            self._conn.commit()

    def archive_modified_time(self, drive_id: str) -> str | None:
        """The archive's Drive mtime, or None if no such archive is indexed."""
        row = self._conn.execute(
            "SELECT modified_time FROM archives WHERE drive_id = ?",
            (drive_id,),
        ).fetchone()
        return row["modified_time"] if row is not None else None
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_scan_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add photolib/db/scan_repo.py tests/test_scan_repo.py
git commit -m "Add ScanRepo.mark_trashed and .archive_modified_time"
```

---

### Task 5: TagsRepo query methods

Note that `mark_synced` takes the slug set and does the comma-join itself, so the on-disk format lives in one place. `ScanRepo.set_enrichment`'s docstring currently points at `sync_tags.py:162` for that format; this task repoints it.

**Files:**
- Modify: `photolib/db/tags_repo.py` (add methods to `TagsRepo`)
- Modify: `photolib/db/scan_repo.py` (the `set_enrichment` docstring reference)
- Test: `tests/test_tags_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, on `TagsRepo`:
  - `.pending_sync(limit: int = 0) -> list[sqlite3.Row]` — rows carry `drive_id, name, synced_tags`; `limit=0` means every candidate
  - `.mark_synced(drive_id: str, slugs: set[str]) -> None`
  - `.orphaned_drive_ids() -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tags_repo.py`:

```python
def _seed_sync(conn):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d1', 'IMG_1.HEIC', 'a')"
    )
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, synced_tags)"
        " VALUES ('d2', 'IMG_2.HEIC', 'b', 'beach')"
    )
    # No tags now, none written last time: not a candidate.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d3', 'IMG_3.HEIC', 'c')"
    )
    # Trashed: never a candidate, even with tags.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, synced_tags,"
        " trashed_at) VALUES ('d4', 'IMG_4.HEIC', 'd', 'x', 'now')"
    )
    conn.commit()

    repo = TagsRepo(conn)
    tag = repo.create("holiday")
    repo.add_files(tag["id"], ["d1"])
    return repo


def test_pending_sync_finds_tagged_and_previously_synced_files(conn):
    repo = _seed_sync(conn)
    assert {row["drive_id"] for row in repo.pending_sync()} == {"d1", "d2"}


def test_pending_sync_excludes_trashed_files(conn):
    repo = _seed_sync(conn)
    assert "d4" not in {row["drive_id"] for row in repo.pending_sync()}


def test_pending_sync_honours_a_limit_and_treats_zero_as_no_limit(conn):
    repo = _seed_sync(conn)
    assert len(repo.pending_sync(limit=1)) == 1
    assert len(repo.pending_sync(limit=0)) == 2


def test_mark_synced_writes_a_sorted_comma_joined_slug_list(conn):
    repo = _seed_sync(conn)
    repo.mark_synced("d1", {"zebra", "apple"})
    row = conn.execute(
        "SELECT synced_tags FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert row["synced_tags"] == "apple,zebra"


def test_mark_synced_writes_an_empty_string_for_no_tags(conn):
    """'Drive holds no tags' is different from 'we never looked'."""
    repo = _seed_sync(conn)
    repo.mark_synced("d2", set())
    row = conn.execute(
        "SELECT synced_tags FROM drive_files WHERE drive_id = 'd2'"
    ).fetchone()
    assert row["synced_tags"] == ""


def test_orphaned_drive_ids_finds_tags_whose_file_is_gone(conn):
    repo = _seed_sync(conn)
    tag = repo.create("ghost")
    repo.add_files(tag["id"], ["d1"])
    conn.execute("DELETE FROM drive_files WHERE drive_id = 'd1'")
    conn.commit()
    assert repo.orphaned_drive_ids() == ["d1"]


def test_orphaned_drive_ids_is_empty_when_every_tagged_file_exists(conn):
    repo = _seed_sync(conn)
    assert repo.orphaned_drive_ids() == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_tags_repo.py -v -k "pending_sync or mark_synced or orphaned"`
Expected: FAIL — `AttributeError: 'TagsRepo' object has no attribute 'pending_sync'`

- [ ] **Step 3: Add the methods**

Append to `TagsRepo` in `photolib/db/tags_repo.py`:

```python
    # ---------- Drive sync ----------

    def pending_sync(self, limit: int = 0) -> list[sqlite3.Row]:
        """Files with tags now, or tags written last time. Trashed excluded.

        A file whose tags were removed still needs a visit, to clear the
        `t_*` appProperties Drive is still carrying — hence the second arm.
        `limit=0` means every candidate.
        """
        sql = (
            "SELECT drive_id, name, synced_tags FROM drive_files "
            "WHERE trashed_at IS NULL AND ("
            "  drive_id IN (SELECT drive_id FROM file_tags) "
            "  OR (synced_tags IS NOT NULL AND synced_tags != '')"
            ") ORDER BY parent_path, name"
        )
        with self._lock:
            if limit > 0:
                return list(self._conn.execute(f"{sql} LIMIT ?", (limit,)))
            return list(self._conn.execute(sql))

    def mark_synced(self, drive_id: str, slugs: set[str]) -> None:
        """Record the slug list now on the file in Drive.

        The comma-joined sorted form is the storage format for
        `drive_files.synced_tags`; it is written here and nowhere else, and
        `ScanRepo.set_enrichment` reads the same shape back from Drive.
        An empty set records "Drive holds no tags", which is not the same
        as NULL's "we never looked".
        """
        with self._lock:
            self._conn.execute(
                "UPDATE drive_files SET synced_tags = ? WHERE drive_id = ?",
                (",".join(sorted(slugs)), drive_id),
            )
            self._conn.commit()

    def orphaned_drive_ids(self) -> list[str]:
        """Tagged drive ids with no matching row in `drive_files`."""
        with self._lock:
            return [
                row["drive_id"]
                for row in self._conn.execute(
                    "SELECT DISTINCT ft.drive_id FROM file_tags ft "
                    "LEFT JOIN drive_files d ON d.drive_id = ft.drive_id "
                    "WHERE d.drive_id IS NULL "
                    "ORDER BY ft.drive_id"
                )
            ]
```

If `TagsRepo.__init__` does not already set `self._lock = conn.lock`, add it, matching `ScanRepo`.

- [ ] **Step 4: Repoint the ScanRepo docstring**

In `photolib/db/scan_repo.py`, inside `set_enrichment`'s docstring, replace:

```
    imported from the file's `t_*` appProperties — the same format
    `sync_tags` writes at `sync_tags.py:162`. Leaving it `None` (the
```

with:

```
    imported from the file's `t_*` appProperties — the same format
    `TagsRepo.mark_synced` writes. Leaving it `None` (the
```

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest tests/test_tags_repo.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add photolib/db/tags_repo.py photolib/db/scan_repo.py tests/test_tags_repo.py
git commit -m "Add TagsRepo sync methods

mark_synced owns the comma-joined storage format, so the shape lives in
one place rather than at a line number ScanRepo's docstring points at."
```

---

### Task 6: Repoint every SQL call site

Nothing moves packages yet. This task only replaces inline SQL with the repo methods added in Tasks 1–5, so a regression here is attributable to the query change and not to a file move.

**Files:**
- Modify: `photolib/buckets.py` (delete two functions)
- Modify: `photolib/repack.py:19-25,37-95,174-183`
- Modify: `photolib/dedupe.py:63-69,104-109`
- Modify: `photolib/actions/verify_library.py:80-90,114-122`
- Modify: `photolib/actions/sync_tags.py:47-58,161-165`
- Modify: `photolib/actions/steps/plan_organize.py:124-131,137`
- Modify: `photolib/api/routes_review.py:29-69,72-81`
- Modify: `tests/test_buckets.py` (drop the two SQL tests, now covered by `test_layout_repo.py`)

**Interfaces:**
- Consumes: every method from Tasks 1–5.
- Produces: no new public names. `photolib/buckets.py` loses `unaccounted_drive_months` and `library_histogram`; `photolib/repack.py` loses `_histogram` and `FOLDER_QUERY`; `photolib/actions/sync_tags.py` loses `_candidates`.

- [ ] **Step 1: Strip SQL from buckets.py**

Delete `unaccounted_drive_months` and `library_histogram` from `photolib/buckets.py` entirely, plus the now-unused `import sqlite3` and `from collections import Counter`. The file keeps `TARGET_SIZE`, `MAX_BUCKET`, `UNKNOWN_FOLDER`, `month_of`, `Bucket`, `pack`, `folder_map` — and no longer touches a database.

Delete the two SQL tests and `_seed` from `tests/test_buckets.py`, and drop `library_histogram` and `unaccounted_drive_months` from its import list. `tests/test_layout_repo.py` covers them now.

- [ ] **Step 2: Repoint repack.py**

In `photolib/repack.py`: delete `FOLDER_QUERY` and `_histogram` entirely, delete `from collections import Counter, defaultdict` down to just `from collections import defaultdict`, and add `from photolib.db.layout_repo import LayoutRepo`.

Rewrite `targets_for`:

```python
def targets_for(conn, exclude: set[str] = frozenset()):
    """Every live catalogued file's bucket target, plus which names already
    sit in each target folder so an arrival can dodge them.

    Public — not just an implementation detail of `plan_moves` — because an
    action reporting the full picture (including files that already sit
    where they belong, not just the ones that must move) needs the same
    `rows`/`targets` this produces.
    """
    repo = LayoutRepo(conn)
    rows = repo.live_files_for_layout(exclude)
    fmap = buckets.folder_map(repo.capture_histogram(exclude))
    targets: dict[str, str] = {}
    # Names already resident per target folder, so arrivals can dodge them.
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        month = buckets.month_of(row["capture"])
        target = fmap[month] if month else buckets.UNKNOWN_FOLDER
        targets[row["drive_id"]] = target
        if target == row["parent_path"]:
            names[target].add(row["name"])
    return rows, targets, names
```

Replace the two `conn.execute` calls and the `conn.commit()` at the end of `apply_move` with one call:

```python
    LayoutRepo(conn).record_move(move.drive_id, move.to_folder, move.new_name)
```

Leave `apply_move`'s `try/except DriveError` replay guard and its docstring exactly as they are.

- [ ] **Step 3: Repoint dedupe.py**

In `photolib/dedupe.py`, add `from photolib.db.media_repo import MediaRepo` and `from photolib.db.scan_repo import ScanRepo`.

In `plan_removals`, replace the `with conn.lock:` block that builds `verified` with:

```python
    verified = MediaRepo(conn).uploaded_drive_ids()
```

In `apply_removal`, replace the `conn.execute(...)` / `conn.commit()` pair with:

```python
    ScanRepo(conn).mark_trashed(removal.drive_id, stamp)
```

Keep the `stamp = datetime.now(timezone.utc).isoformat()` line and the docstring.

- [ ] **Step 4: Repoint verify_library.py**

In `photolib/actions/verify_library.py`, add `from photolib.db.media_repo import MediaRepo` and `from photolib.db.tags_repo import TagsRepo`.

Replace the `uploaded = list(ctx.conn.execute(...))` block with:

```python
    uploaded = MediaRepo(ctx.conn).uploaded_with_names()
```

Replace the `orphans = [...]` list comprehension with:

```python
    orphans = TagsRepo(ctx.conn).orphaned_drive_ids()
```

Leave the long comment above `missing, moved, mismatched, unconfirmed` untouched.

- [ ] **Step 5: Repoint sync_tags.py**

In `photolib/actions/sync_tags.py`, delete the `_candidates` function entirely. Replace its call:

```python
    rows = _candidates(ctx.conn, params.limit)
```

with:

```python
    rows = tags.pending_sync(params.limit)
```

and change the line above it from `desired_by_file = TagsRepo(ctx.conn).slugs_by_file()` to:

```python
    tags = TagsRepo(ctx.conn)
    desired_by_file = tags.slugs_by_file()
```

Replace the `ctx.conn.execute("UPDATE drive_files SET synced_tags ...")` / `ctx.conn.commit()` pair with:

```python
        tags.mark_synced(drive_id, desired)
```

`desired` is already the slug set at that point, so the `",".join(sorted(desired))` disappears — `mark_synced` does it.

- [ ] **Step 6: Repoint plan_organize.py**

In `photolib/actions/steps/plan_organize.py`, replace the two inline lookups:

```python
        sidecar = None
        if row["sidecar_id"]:
            sidecar = media_repo.sidecar(row["sidecar_id"])
        archive_modified = scan_repo.archive_modified_time(
            row["archive_drive_id"]
        )
```

`media_repo` and `scan_repo` are already in scope at that point in `run`.

Replace the histogram call:

```python
    counts = LayoutRepo(ctx.conn).unaccounted_months()
```

and add `from photolib.db.layout_repo import LayoutRepo` to the imports. `buckets` is still imported for `month_of` and `folder_map`.

- [ ] **Step 7: Repoint routes_review.py**

In `photolib/api/routes_review.py`, rewrite the `media` endpoint body:

```python
@router.get("/review/media")
def media(
    request: Request,
    limit: int = 200,
    offset: int = 0,
    folder: str | None = None,
    duplicates_only: bool = False,
) -> dict:
    page = MediaRepo(request.app.state.conn).review_page(
        folder=folder,
        duplicates_only=duplicates_only,
        limit=limit,
        offset=offset,
    )
    return {
        "total": page["total"],
        "rows": [
            {**{f: row[f] for f in ROW_FIELDS}, "size": row["entry_size"]}
            for row in page["rows"]
        ],
    }
```

and the `retry` endpoint body:

```python
@router.post("/review/retry/{entry_id}")
def retry(request: Request, entry_id: int) -> dict:
    """Queue a failed file for another attempt, forgetting the last one."""
    repo = MediaRepo(request.app.state.conn)
    if not repo.exists(entry_id):
        raise HTTPException(status_code=404, detail="no such media entry")
    repo.reset_upload(entry_id)
    return {"entry_id": entry_id, "upload_status": "pending"}
```

- [ ] **Step 8: Verify no SQL remains outside db/**

Run:

```bash
grep -rn "conn.execute\|conn.executemany\|conn.executescript" photolib --include="*.py" | grep -v "^photolib/db/"
```

Expected: no output. If a line appears, it was missed above — move it behind a repo method before continuing.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Route every query through a repo

SQL now lives only in photolib/db/. The eight modules that carried their
own queries call repo methods instead; buckets.py no longer touches a
database at all, and sync_tags' _candidates becomes TagsRepo.pending_sync."
```

---

### Task 7: The planning package

Pure moves and one split. No behaviour changes.

**Files:**
- Create: `photolib/planning/__init__.py`
- Move: `photolib/takeout.py` → `photolib/planning/takeout.py`
- Move: `photolib/buckets.py` → `photolib/planning/buckets.py`
- Move: `photolib/enrich.py` → `photolib/planning/enrich.py`
- Create: `photolib/planning/layout.py` (planning half of `repack.py`)
- Create: `photolib/planning/duplicates.py` (planning half of `dedupe.py`)
- Modify: `photolib/actions/reorganize_library.py:17`, `photolib/actions/steps/plan_organize.py:17`, `photolib/actions/steps/pair_metadata.py:13`
- Test: rename `tests/test_buckets.py` → `tests/test_planning_buckets.py`; create `tests/test_planning_layout.py`, `tests/test_planning_duplicates.py`

**Interfaces:**
- Consumes: Task 6's repo-backed `targets_for`, `plan_removals`.
- Produces:
  - `photolib.planning.layout`: `Move`, `targets_for(conn, exclude)`, `moves_from_targets(rows, targets, names)`, `plan_moves(conn, *, exclude)` *(the signature changes in Task 10, not here)*, `plan_sweep(drive, root_id)`, `folder_paths(drive, root_id)`
  - `photolib.planning.duplicates`: `Removal`, `plan_removals(drive, conn, root_id)`
  - `photolib.planning.buckets`, `.takeout`, `.enrich`: unchanged public names

- [ ] **Step 1: Create the package and move the three unchanged modules**

```bash
mkdir -p photolib/planning
touch photolib/planning/__init__.py
git mv photolib/takeout.py photolib/planning/takeout.py
git mv photolib/buckets.py photolib/planning/buckets.py
git mv photolib/enrich.py photolib/planning/enrich.py
git mv tests/test_buckets.py tests/test_planning_buckets.py
git add photolib/planning/__init__.py
```

- [ ] **Step 2: Split repack.py into planning/layout.py**

```bash
git mv photolib/repack.py photolib/planning/layout.py
```

Then in `photolib/planning/layout.py`, delete `apply_move`, `ensure_folders` and `apply_sweep` (they go to `execution/moves.py` in Task 8 — copy them somewhere first, or recover them from `git show HEAD:photolib/repack.py`). Update the module docstring and imports:

```python
"""Where every live file belongs: its bucket folder, and the folders left
empty once everything has moved.

Planning only — nothing here mutates Drive. `photolib.execution.moves`
enacts what these functions decide.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

from photolib.db.layout_repo import LayoutRepo
from photolib.planning import buckets
```

The `from photolib.drive.errors import DriveError` import goes away with `apply_move`.

- [ ] **Step 3: Split dedupe.py into planning/duplicates.py**

```bash
git mv photolib/dedupe.py photolib/planning/duplicates.py
```

In `photolib/planning/duplicates.py`, delete `apply_removal` and the now-unused `from datetime import datetime, timezone` and `from photolib.db.scan_repo import ScanRepo` imports. Keep `Removal`, `_walk`, `plan_removals`, and the `MediaRepo` import. Append to the module docstring:

```
Planning only — `photolib.execution.trash` does the trashing.
```

- [ ] **Step 4: Update the importers**

- `photolib/actions/reorganize_library.py:17` — `from photolib import dedupe, enrich, places, repack, scan` becomes:

```python
from photolib import places, scan
from photolib.planning import duplicates, enrich, layout
```

`scan` stays for now; Task 9 renames it to `ingest` and finishes this line.

Then replace every `dedupe.` with `duplicates.` and every `repack.` with `layout.` in that file — lines 186, 205, 240, 266, 267, 298, 324, 329, and the comment at 262-264.

- `photolib/actions/steps/plan_organize.py:17` — `from photolib import buckets, places, takeout` becomes:

```python
from photolib import places
from photolib.planning import buckets, takeout
```

- `photolib/actions/steps/pair_metadata.py:13` — `from photolib import archives, takeout` becomes:

```python
from photolib import archives
from photolib.planning import takeout
```

- [ ] **Step 5: Split the tests**

```bash
git mv tests/test_repack.py tests/test_planning_layout.py
git mv tests/test_dedupe.py tests/test_planning_duplicates.py
```

In `tests/test_planning_layout.py`: change the import to `from photolib.planning.layout import Move, plan_moves, plan_sweep`, then **move into a new `tests/test_execution_moves.py`** every test that calls `apply_move` or `apply_sweep` — as of now that is `test_apply_move_is_safe_to_replay_when_drive_rejects_a_stale_removeparents`, `test_apply_move_reraises_a_genuine_drive_failure`, `test_apply_sweep_is_safe_to_replay`, and the test containing the `apply_move` call at line 177 — together with the writer and drive fakes those tests use. Task 8 supplies the import line for the new file.

In `tests/test_planning_duplicates.py`: change the import to `from photolib.planning.duplicates import Removal, plan_removals`, and move `test_apply_removal_is_safe_to_replay`, `test_apply_removal_stamps_the_catalog_with_the_trash_time`, and the `_IdempotentTrashWriter` helper into a new `tests/test_execution_trash.py`.

Both new test files will fail to import until Task 8 creates their modules. Create them in this task with the import line commented out and a `pytest.skip("execution package lands in Task 8", allow_module_level=True)` at the top, then remove the skip in Task 8.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, with `test_execution_moves.py` and `test_execution_trash.py` reported as skipped.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Create photolib/planning

takeout, buckets and enrich move unchanged. repack and dedupe split: the
planning halves land here as layout.py and duplicates.py, the applying
halves follow in the next commit."
```

---

### Task 8: The execution package

**Files:**
- Create: `photolib/execution/__init__.py`
- Move: `photolib/transfer.py` → `photolib/execution/transfer.py`
- Move: `photolib/downloads.py` → `photolib/execution/downloads.py`
- Create: `photolib/execution/moves.py` (applying half of the old `repack.py`)
- Create: `photolib/execution/trash.py` (applying half of the old `dedupe.py`)
- Modify: `photolib/actions/steps/organize.py:27,29`, `photolib/api/app.py:15`, `photolib/api/routes_downloads.py:9`, `photolib/actions/reorganize_library.py`
- Test: `tests/test_execution_moves.py`, `tests/test_execution_trash.py`, and the import lines in `tests/test_transfer.py`, `tests/test_downloads.py`, `tests/test_action_organize.py`, `tests/test_live_phase3.py`

**Interfaces:**
- Consumes: `photolib.planning.layout.Move`, `photolib.planning.duplicates.Removal`, `LayoutRepo.record_move`, `ScanRepo.mark_trashed`.
- Produces:
  - `photolib.execution.moves`: `apply_move(writer, conn, move, folder_ids, drive=None)`, `ensure_folders(writer, root_id, folders)`, `apply_sweep(writer, folder_id)`
  - `photolib.execution.trash`: `apply_removal(writer, removal, conn)`
  - `photolib.execution.transfer`, `.downloads`: unchanged public names

- [ ] **Step 1: Create the package and move the two unchanged modules**

```bash
mkdir -p photolib/execution
touch photolib/execution/__init__.py
git mv photolib/transfer.py photolib/execution/transfer.py
git mv photolib/downloads.py photolib/execution/downloads.py
git add photolib/execution/__init__.py
```

- [ ] **Step 2: Create execution/moves.py**

Recover the three functions from `git show HEAD~1:photolib/repack.py` and put them in `photolib/execution/moves.py`:

```python
"""Enacting a folder-layout plan: reparent a file, make a bucket folder,
trash a folder left empty.

Every function here takes a writer. `photolib.planning.layout` decides
what these should be called with.
"""

from __future__ import annotations

from photolib.db.layout_repo import LayoutRepo
from photolib.drive.errors import DriveError
from photolib.planning.layout import Move
```

followed by `apply_move`, `ensure_folders` and `apply_sweep` verbatim as they stood after Task 6 — `apply_move` already calls `LayoutRepo(conn).record_move(...)`. Update `apply_sweep`'s docstring reference from `dedupe.apply_removal` to `photolib.execution.trash.apply_removal`.

- [ ] **Step 3: Create execution/trash.py**

```python
"""Trashing a redundant copy the duplicate plan named.

Takes a writer. `photolib.planning.duplicates` decides what to trash.
"""

from __future__ import annotations

from datetime import datetime, timezone

from photolib.db.scan_repo import ScanRepo
from photolib.planning.duplicates import Removal
```

followed by `apply_removal` verbatim as it stood after Task 6.

- [ ] **Step 4: Update the importers**

- `photolib/actions/steps/organize.py:27` → `from photolib.execution.downloads import InflightRegistry, run_folder_name, sweep_empty`
- `photolib/actions/steps/organize.py:29` → `from photolib.execution.transfer import TransferError, mime_for, transfer_entry`
- `photolib/api/app.py:15` → `from photolib.execution.downloads import InflightRegistry`
- `photolib/api/routes_downloads.py:9` → `from photolib.execution.downloads import observe, stale_runs`
- `photolib/actions/reorganize_library.py` — add `from photolib.execution import moves, trash`, then change `layout.apply_move` → `moves.apply_move`, `layout.ensure_folders` → `moves.ensure_folders`, `layout.apply_sweep` → `moves.apply_sweep`, and `duplicates.apply_removal` → `trash.apply_removal`.

- [ ] **Step 5: Update the test imports**

- `tests/test_transfer.py:8` and `tests/test_action_organize.py:159` → `from photolib.execution import transfer`
- `tests/test_live_phase3.py:17` → `from photolib.execution import transfer`
- `tests/test_action_organize.py:22` → `from photolib.execution.downloads import InflightRegistry, observe`
- `tests/test_action_organize.py:23` → `from photolib.execution.transfer import TransferError`
- `tests/test_downloads.py:6` → `from photolib.execution.downloads import (`
- `tests/test_execution_moves.py` — remove the `pytest.skip` and add `from photolib.execution.moves import apply_move, apply_sweep` plus `from photolib.planning.layout import Move`
- `tests/test_execution_trash.py` — remove the `pytest.skip` and add `from photolib.execution.trash import apply_removal` plus `from photolib.planning.duplicates import Removal`

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, no skips from the two new files.

- [ ] **Step 7: Verify planning never imports execution**

Run:

```bash
grep -rn "photolib.execution" photolib/planning/
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Create photolib/execution

transfer and downloads move unchanged; the applying halves of repack and
dedupe land as moves.py and trash.py. Every function in the package takes
a writer, and nothing in planning/ imports it."
```

---

### Task 9: Absorb the three adapters

**Files:**
- Move: `photolib/thumbs.py` → `photolib/drive/thumbs.py`
- Move: `photolib/archives.py` → `photolib/ziparchive/source.py`
- Move: `photolib/scan.py` → `photolib/ingest.py`
- Modify: `photolib/api/app.py:21`, `photolib/api/routes_thumbs.py:8`, `photolib/actions/steps/pair_metadata.py:13`, `photolib/actions/steps/scan_archives.py:11,16`, `photolib/actions/reorganize_library.py:17`
- Test: `tests/test_thumbs.py:3`, `tests/test_archives.py:3`, `tests/test_live_drive.py:11`, `tests/test_scan_destination.py:1`, `tests/test_action_reorganize_library.py:309`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `photolib.drive.thumbs` (`ThumbnailCache`, `ThumbnailUnavailable`, `SIZES`), `photolib.ziparchive.source` (`drive_range_reader` and the rest of the old `archives` surface), `photolib.ingest` (`index_destination`).

- [ ] **Step 1: Move the files**

```bash
git mv photolib/thumbs.py photolib/drive/thumbs.py
git mv photolib/archives.py photolib/ziparchive/source.py
git mv photolib/scan.py photolib/ingest.py
```

- [ ] **Step 2: Fix the moved modules' own imports**

`photolib/ziparchive/source.py` imports `from photolib.ziparchive.reader import (...)`. Leave it absolute — it still resolves.

Update `photolib/ingest.py`'s docstring first line to say what it now is:

```python
"""Indexing the destination folder into the catalog.

Reads Drive and fills `drive_files`; it neither plans nor mutates Drive,
which is why it sits beside `config.py` rather than in `planning/` or
`execution/`. Shared by Scan Archives and Reorganize Folders.

The whole tree is collected before anything is written, because
```

Keep the rest of the existing docstring body.

- [ ] **Step 3: Update the importers**

- `photolib/api/app.py:21` → `from photolib.drive.thumbs import ThumbnailCache`
- `photolib/api/routes_thumbs.py:8` → `from photolib.drive.thumbs import ThumbnailUnavailable`
- `photolib/actions/steps/pair_metadata.py:13` → `from photolib.ziparchive import source as archives` *(keeps the `archives.` call sites in that file working unchanged)*
- `photolib/actions/steps/scan_archives.py:11` → `from photolib.ziparchive import source as archives`
- `photolib/actions/steps/scan_archives.py:16` → `from photolib.ingest import index_destination`
- `photolib/actions/reorganize_library.py:17` → finish the line started in Task 7: `from photolib import ingest, places`, and change the `scan.index_destination(...)` call site to `ingest.index_destination(...)`

- [ ] **Step 4: Update the test imports**

- `tests/test_thumbs.py:3` → `from photolib.drive.thumbs import SIZES, ThumbnailCache, ThumbnailUnavailable`
- `tests/test_archives.py:3` → `from photolib.ziparchive import source as archives`
- `tests/test_live_drive.py:11` → `from photolib.ziparchive import source as archives`
- `tests/test_scan_destination.py:1` → `from photolib.ingest import index_destination`
- `tests/test_action_reorganize_library.py:309` → `from photolib import ingest as scan_module`

- [ ] **Step 5: Confirm the top level is clean**

Run:

```bash
ls photolib/*.py
```

Expected exactly: `photolib/__init__.py`, `photolib/config.py`, `photolib/ingest.py`, `photolib/main.py`, `photolib/places.py`.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Fold the three adapters into the packages they adapt

thumbs.py caches Drive's renders, so it joins drive/. archives.py bridges
Drive and the ZIP reader, so it joins ziparchive/ as source.py. scan.py
reads Drive and fills the catalog — neither plan nor apply — so it stays
top-level, renamed ingest.py to say so."
```

---

### Task 10: Drop plan_moves' dead parameters

`plan_moves(drive, conn, root_id, *, exclude)` reads neither `drive` nor `root_id`. Six of its seven call sites are tests constructing a `FakeDrive()` for a parameter that is never touched.

**Files:**
- Modify: `photolib/planning/layout.py`
- Modify: `photolib/actions/reorganize_library.py:205`
- Test: `tests/test_planning_layout.py:87,92,113,117,134,161,193`

**Interfaces:**
- Consumes: `photolib.planning.layout.plan_moves` from Task 7.
- Produces: `plan_moves(conn, *, exclude: set[str] = frozenset()) -> list[Move]`.

- [ ] **Step 1: Change the signature**

In `photolib/planning/layout.py`:

```python
def plan_moves(conn, *, exclude: set[str] = frozenset()) -> list[Move]:
    """Every live catalogued file whose bucket target differs from where it
    currently sits, renamed as needed to avoid colliding with a file
    already at that destination or with another move landing there first.

    `exclude` drops files the duplicate plan is about to trash from
    consideration and from the space they would otherwise reserve.
    """
    rows, targets, names = targets_for(conn, exclude)
    return moves_from_targets(rows, targets, names)
```

- [ ] **Step 2: Update the production call site**

`photolib/actions/reorganize_library.py:205`:

```python
        moves = layout.plan_moves(ctx.conn, exclude=doomed)
```

- [ ] **Step 3: Update the test call sites**

In `tests/test_planning_layout.py`, replace every `plan_moves(FakeDrive(), conn, "root")` with `plan_moves(conn)`, and `plan_moves(FakeDrive(), conn, "root", exclude=doomed)` with `plan_moves(conn, exclude=doomed)`. If `FakeDrive` becomes unreferenced in that file, delete it.

- [ ] **Step 4: Verify no call site was missed**

Run:

```bash
grep -rn "plan_moves(" photolib tests --include="*.py"
```

Expected: every hit passes `conn` as the first argument, and none passes a drive or a root id.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Drop plan_moves' unused drive and root_id parameters

Neither was read. Six of the seven call sites were tests building a
FakeDrive for a parameter the function never touched."
```

---

### Task 11: Enforce the layering

Two pre-existing leaks must be closed before the contract can pass: `photolib/db/scan_repo.py` imports `ziparchive.reader.ZipEntry`, and `photolib/planning/enrich.py` imports `drive.client.DriveFile`. Both are type annotations over a handful of attributes, so a `Protocol` on the consumer side removes the import without changing a single call site.

**Files:**
- Modify: `pyproject.toml`
- Modify: `photolib/db/scan_repo.py:9,66-68`
- Modify: `photolib/planning/enrich.py:11-13,29`
- Test: `tests/test_architecture.py` (new)

**Interfaces:**
- Consumes: the package layout from Tasks 7–9.
- Produces: `photolib.db.scan_repo.ArchiveEntry` (Protocol), `photolib.planning.enrich.DriveFileLike` (Protocol).

- [ ] **Step 1: Add the dev dependency**

In `pyproject.toml`, add to `[project.optional-dependencies].dev`:

```toml
    "import-linter>=2.0",
```

Then: `uv sync --extra dev`

- [ ] **Step 2: Write the contract**

Append to `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "photolib"

[[tool.importlinter.contracts]]
name = "Layers run one way"
type = "layers"
layers = [
    "photolib.api",
    "photolib.actions",
    "photolib.execution",
    "photolib.planning",
    "photolib.db",
    "photolib.config",
]

[[tool.importlinter.contracts]]
name = "Planning decides, it never mutates"
type = "forbidden"
source_modules = ["photolib.planning"]
forbidden_modules = ["photolib.execution"]

[[tool.importlinter.contracts]]
name = "Persistence does not know what a ZIP archive is"
type = "forbidden"
source_modules = ["photolib.db"]
forbidden_modules = ["photolib.ziparchive"]

[[tool.importlinter.contracts]]
name = "Planning does not depend on the Drive transport"
type = "forbidden"
source_modules = ["photolib.planning"]
forbidden_modules = ["photolib.drive"]
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run lint-imports`
Expected: FAIL — two broken contracts, naming `photolib.db.scan_repo -> photolib.ziparchive.reader` and `photolib.planning.enrich -> photolib.drive.client`.

- [ ] **Step 4: Close the ZipEntry leak**

In `photolib/db/scan_repo.py`, delete `from photolib.ziparchive.reader import ZipEntry` and add, after the `_ENTRY_COLUMNS` block:

```python
class ArchiveEntry(Protocol):
    """The shape `replace_entries` stores. `ziparchive.reader.ZipEntry`
    satisfies it; naming the shape rather than the class keeps the
    persistence layer from depending on the ZIP parser."""

    path: str
    name: str
    crc32: int
    size: int
    compressed_size: int
    method: int
    local_header_offset: int
```

Add `from typing import Protocol` to the imports, and change `replace_entries`' annotation:

```python
    def replace_entries(
        self, archive_id: int, entries: list[ArchiveEntry], kinds: dict[str, str]
    ) -> None:
```

- [ ] **Step 5: Close the DriveFile leak**

In `photolib/planning/enrich.py`, delete `from photolib.drive.client import DriveFile` and add:

```python
from typing import Protocol


class DriveFileLike(Protocol):
    """What `enrichment_for` reads off a Drive file.
    `drive.client.DriveFile` satisfies it."""

    app_properties: dict | None

    def location(self) -> tuple[float, float] | None: ...

    def capture(self) -> tuple[int | None, str]: ...
```

and change the signature to `def enrichment_for(file: DriveFileLike, geocoder) -> Enrichment:`.

- [ ] **Step 6: Run the contract again**

Run: `uv run lint-imports`
Expected: PASS — 4 contracts, 0 broken.

- [ ] **Step 7: Make the contract a test**

Create `tests/test_architecture.py`:

```python
"""The layering contract, run as a test so `pytest` alone catches drift."""

import subprocess


def test_import_contracts_hold():
    # The console script, not `python -m importlinter`: import-linter's entry
    # point is a click command with no `__main__`, and `uv run pytest` puts
    # the venv's bin directory on PATH.
    result = subprocess.run(["lint-imports"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, including `test_architecture.py`.

- [ ] **Step 9: Update the README architecture section**

In `README.md`, replace the `## Architecture` bullet list with:

```markdown
- `photolib/planning/` — decides what should happen: bucket layout, duplicate
  detection, Takeout naming rules, metadata enrichment. Takes readers, returns
  a plan; nothing here mutates Drive
- `photolib/execution/` — enacts a plan: file transfer, reparenting, trashing,
  the downloads folder. Every function takes a writer
- `photolib/db/` — SQLite catalog holding settings, archive indexes, and jobs.
  The only place SQL lives
- `photolib/drive/` — OAuth token refresh, a REST client over `httpx`, and a
  disk cache in front of Drive's thumbnail renderer
- `photolib/ziparchive/` — reads ZIP indexes and extracts single entries using
  HTTP byte ranges, so a 2.15 GB archive is never downloaded to retrieve one photo
- `photolib/ingest.py` — walks the destination folder into the catalog
- `photolib/actions/` — one module per capability; each becomes a page in the UI
- `photolib/jobs/` — a background worker that runs actions and streams progress
- `photolib/api/` — FastAPI routes
- `web/` — React + Vite frontend

The dependency direction is enforced by `import-linter`; see
`[tool.importlinter]` in `pyproject.toml`. `uv run lint-imports` checks it, and
`tests/test_architecture.py` runs it as part of the suite.
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Enforce the layering with import-linter

Closes the two pre-existing leaks the contract exposes: scan_repo named
ziparchive's ZipEntry and enrich named drive.client's DriveFile, both only
as annotations over a few attributes. Protocols on the consumer side drop
the imports without touching a call site."
```

---

## Verification

After Task 11, all of these must hold:

```bash
uv run pytest                       # green, live tests deselected
uv run lint-imports                 # 4 contracts, 0 broken
ls photolib/*.py                    # __init__ config ingest main places only
grep -rn "conn.execute" photolib --include="*.py" | grep -v "^photolib/db/"   # empty
grep -rn "photolib.execution" photolib/planning/                             # empty
git log --oneline --follow photolib/planning/layout.py                       # history reaches repack.py
```

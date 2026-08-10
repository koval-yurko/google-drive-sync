# Phase 2: Knowledge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Scan, Pair and Plan actions plus the Review page, so every one of the 1,284 media files in the Takeout export has a resolved capture date, place, duplicate verdict and destination — reviewable in the browser before a single byte moves.

**Architecture:** Three actions run in sequence, each writing to the SQLite catalog and each independently re-runnable. Scan indexes all 17 ZIP central directories over byte ranges and walks the destination folder. Pair extracts and parses the sidecars — roughly 1 KB each — and matches them to their media across archive parts. Plan resolves dates, places, duplicate verdicts and target paths without touching Drive at all. The Review page reads the result. Nothing in this phase writes to Drive.

**Tech Stack:** Python 3.12 via `uv`, stdlib `sqlite3` and `zlib`, `httpx` for the Geocoding API, pydantic v2; React 18 + TypeScript + Vite; pytest, Vitest.

## Global Constraints

- Python 3.12, managed exclusively through `uv`. Never invoke system `python3` (it is 3.9 and will fail).
- All Drive REST calls go through `httpx` via the existing `DriveClient`. Do not add `google-api-python-client`.
- Credentials live at repo root: `credentials.json` and `token.json`. Both are gitignored and MUST NEVER be committed, printed to logs, or included in test fixtures.
- `GOOGLE_MAPS_API_KEY` is read from the environment or the gitignored `.env`. It MUST NEVER be committed, logged, or written into a test fixture.
- No test may perform a real network call. Live tests are opt-in only, marked `@pytest.mark.live`, and deselected by default.
- SQLite access is stdlib `sqlite3` with `row_factory = sqlite3.Row`. No ORM.
- Backend package is `photolib`. Frontend lives in `web/`.
- **This phase writes nothing to Drive.** Every Drive call is a read. Any task that appears to need a write is a plan error — stop and raise it.
- Actions are discovered by `photolib/actions/registry.py`. Every action module MUST declare `ID`, `TITLE`, `DESCRIPTION`, `ORDER`, a `Params` class extending `ActionParams`, and a `run(ctx, params)` **generator** function. A non-generator `run` is silently skipped by the registry.

## Decisions that override the spec

The spec was written before the destination folder was inspected. Two of its
assumptions no longer hold, and the operator has ruled on both. Where this
section conflicts with `docs/superpowers/specs/2026-08-09-google-photos-organizer-design.md`, **this section wins.**

1. **The destination is not empty.** `photos_root` ("Photos", id `1lREjJeh6Kzlj1ep7cUWv-gQAPu3iTHMN`) already holds 2,384 files / 71.9 GB in seven `back_YYYY_MM` folders. Measured overlap with the export: **501 of 1,284 names already present, 480 of them at identical size, 21 at a different size.**
2. **Organised photos go to new `photos_root/YYYY-MM/` folders.** The `back_*` folders are never read from as a destination, never written to, never renamed, never moved. They are indexed for information only.
3. **Duplicates are detected but never skipped.** The spec said duplicate losers are marked `skipped` and never uploaded. That is overridden: every media file gets `upload_status = 'pending'` regardless of verdict. `duplicate_of` and `duplicate_reason` are recorded and displayed so a later dedupe pass has the data, but nothing is withheld from upload. There is no code path in this phase that sets `upload_status = 'skipped'`.
4. **EXIF date extraction is out of scope.** The spec listed embedded EXIF as the second date source. It is dropped: reading EXIF means range-reading every media file, and the measurements show only 15 of 1,284 files lack a sidecar. Those 15 fall back to the year folder and are flagged on the Review page with `capture_source = 'year_folder'`. If that proves insufficient, add EXIF in a later phase.

---

## File Structure

**Backend**

| File | Responsibility |
| --- | --- |
| `photolib/db/schema.sql` | Extended with the four Phase 2 tables |
| `photolib/db/catalog.py` | `SCHEMA_VERSION` bumped to 2 |
| `photolib/db/scan_repo.py` | Persist archives, entries, and indexed Drive files |
| `photolib/db/media_repo.py` | Persist sidecars, media rows, and the review query |
| `photolib/takeout.py` | Pure Takeout naming rules — no I/O, no Drive, no DB |
| `photolib/places.py` | Coordinate rounding, geocache lookup, Geocoding API client |
| `photolib/actions/scan_archives.py` | Action: index archives and destination |
| `photolib/actions/pair_metadata.py` | Action: extract and match sidecars |
| `photolib/actions/plan_organize.py` | Action: resolve dates, places, verdicts, targets |
| `photolib/api/routes_review.py` | `GET /api/review/summary`, `GET /api/review/media` |

**Frontend**

| File | Responsibility |
| --- | --- |
| `web/src/pages/ReviewPage.tsx` | Summary tiles plus a filterable table of every file |
| `web/src/api/client.ts` | Extended with `getReviewSummary()` and `listReviewMedia()` |
| `web/src/api/types.ts` | Extended with `ReviewSummary` and `ReviewMedia` |
| `web/src/App.tsx` | One route added for `/review` |
| `web/src/components/Nav.tsx` | One link added for Review |

**Tests**

| File | Covers |
| --- | --- |
| `tests/test_takeout.py` | Naming rules, table-driven against real Takeout quirks |
| `tests/test_scan_repo.py`, `tests/test_media_repo.py` | Persistence |
| `tests/test_places.py` | Rounding, cache hits, API parsing, missing key |
| `tests/test_action_scan.py`, `tests/test_action_pair.py`, `tests/test_action_plan.py` | The three actions against `FakeDrive` |
| `tests/test_api_review.py` | Review routes |
| `web/src/pages/ReviewPage.test.tsx` | Review table rendering and filtering |

---

## Task 1: Catalog schema v2

**Files:**
- Modify: `photolib/db/schema.sql`
- Modify: `photolib/db/catalog.py`
- Create: `tests/test_catalog_v2.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect`.
- Produces: tables `sidecars`, `media`, `drive_files`, `geocache`; `catalog.SCHEMA_VERSION == 2`.

- [x] **Step 1: Write the failing test**

Create `tests/test_catalog_v2.py`:

```python
import sqlite3

import pytest

from photolib.db import catalog


def test_phase_two_tables_exist(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"sidecars", "media", "drive_files", "geocache"} <= names


def test_schema_version_is_two(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert catalog.SCHEMA_VERSION == 2


def test_upgrade_from_v1_keeps_existing_rows(tmp_path):
    db = tmp_path / "t.db"
    conn = catalog.connect(db)
    conn.execute("INSERT INTO settings (key, value) VALUES ('photos_root', 'x')")
    conn.commit()
    conn.execute("PRAGMA user_version = 1")   # pretend this is an old catalog
    conn.commit()
    conn.close()

    upgraded = catalog.connect(db)
    assert upgraded.execute("SELECT value FROM settings").fetchone()["value"] == "x"
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 2


def test_media_requires_a_known_entry(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO media (entry_id) VALUES (9999)")
        conn.commit()


def test_upload_status_rejects_unknown_values(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES (1,'a/b.HEIC','b.HEIC',1,2,3,8,0,'media')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO media (entry_id, upload_status) VALUES (1, 'nonsense')")
        conn.commit()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_catalog_v2.py -v`
Expected: FAIL — `sidecars` is not in the table list, and `SCHEMA_VERSION` is 1.

- [x] **Step 3: Append the Phase 2 tables to the schema**

Append to `photolib/db/schema.sql`, keeping everything already there:

```sql
CREATE TABLE IF NOT EXISTS sidecars (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id         INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
    title            TEXT,
    photo_taken_time INTEGER,
    creation_time    INTEGER,
    latitude         REAL,
    longitude        REAL,
    altitude         REAL,
    url              TEXT,
    device           TEXT,
    raw_json         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id         INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
    sidecar_id       INTEGER REFERENCES sidecars(id) ON DELETE SET NULL,
    capture_time     INTEGER,
    capture_source   TEXT,
    latitude         REAL,
    longitude        REAL,
    place            TEXT,
    country          TEXT,
    target_folder    TEXT,
    target_name      TEXT,
    duplicate_of     TEXT,
    duplicate_reason TEXT,
    upload_status    TEXT NOT NULL DEFAULT 'pending'
                     CHECK (upload_status IN ('pending', 'done', 'error')),
    drive_file_id    TEXT,
    md5              TEXT,
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_media_target ON media(target_folder, target_name);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(upload_status);

CREATE TABLE IF NOT EXISTS drive_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    md5         TEXT,
    size        INTEGER,
    indexed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_drive_files_name ON drive_files(name);

CREATE TABLE IF NOT EXISTS geocache (
    key      TEXT PRIMARY KEY,
    place    TEXT,
    country  TEXT,
    raw_json TEXT
);
```

Note the `upload_status` CHECK deliberately has no `'skipped'` value. Duplicates
are recorded, never withheld — see "Decisions that override the spec".

- [x] **Step 4: Bump the schema version**

In `photolib/db/catalog.py`, change the constant:

```python
SCHEMA_VERSION = 2
```

The existing `_migrate` re-runs the whole script when `user_version` is behind,
and every statement is `IF NOT EXISTS`, so upgrading a v1 catalog adds the new
tables and leaves existing rows untouched.

- [x] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_catalog_v2.py -v`
Expected: 5 passed

- [x] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass — 107 existing plus 5 new.

- [x] **Step 7: Commit**

```bash
git add photolib/db/schema.sql photolib/db/catalog.py tests/test_catalog_v2.py
git commit -m "feat: catalog schema v2 with sidecars, media, drive files, and geocache"
```

---

## Task 2: Takeout naming rules

The single most bug-prone piece of the project, and the cheapest to test: it is
pure string handling. 88% of sidecars live in a different archive part from
their media, so matching cannot rely on directory adjacency.

**Files:**
- Create: `photolib/takeout.py`
- Create: `tests/test_takeout.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `photolib.takeout.media_name_for_sidecar(sidecar_name: str) -> str` — the media filename a sidecar describes.
  - `photolib.takeout.is_truncated(sidecar_name: str) -> bool` — True when Takeout cut the name at its 51-character limit.
  - `photolib.takeout.stem(name: str) -> str` — filename without its final extension, duplicate index preserved.
  - `photolib.takeout.year_from_path(path: str) -> int | None` — the year in a `Photos from YYYY` folder.
  - `photolib.takeout.is_live_photo_pair(a: str, b: str) -> bool` — same stem, one still image and one video.

- [x] **Step 1: Write the failing test**

Create `tests/test_takeout.py`:

```python
import pytest

from photolib import takeout


@pytest.mark.parametrize(
    "sidecar, expected",
    [
        # the ordinary case
        ("IMG_9004.MOV.supplemental-metadata.json", "IMG_9004.MOV"),
        # the older, shorter form
        ("IMG_9004.MOV.json", "IMG_9004.MOV"),
        # the duplicate index sits OUTSIDE the extension, on both sides
        ("IMG_7324.PNG.supplemental-metadata(1).json", "IMG_7324(1).PNG"),
        ("IMG_7324.PNG(1).json", "IMG_7324(1).PNG"),
        # Takeout truncates the sidecar name at 51 characters, cutting the
        # "supplemental-metadata" marker part-way through
        ("IMG_1234.HEIC.supplemental-met.json", "IMG_1234.HEIC"),
        ("IMG_1234.HEIC.supp.json", "IMG_1234.HEIC"),
        # case is not normalised away
        ("photo.jpeg.supplemental-metadata.json", "photo.jpeg"),
    ],
)
def test_media_name_for_sidecar(sidecar, expected):
    assert takeout.media_name_for_sidecar(sidecar) == expected


def test_media_name_for_sidecar_rejects_non_sidecars():
    with pytest.raises(ValueError):
        takeout.media_name_for_sidecar("IMG_9004.MOV")


def test_is_truncated():
    assert takeout.is_truncated("a" * 47 + ".json") is True
    assert takeout.is_truncated("short.HEIC.json") is False


def test_stem_keeps_the_duplicate_index():
    assert takeout.stem("IMG_7324(1).PNG") == "IMG_7324(1)"
    assert takeout.stem("IMG_7324.PNG") == "IMG_7324"
    assert takeout.stem("no-extension") == "no-extension"


@pytest.mark.parametrize(
    "path, year",
    [
        ("Takeout/Google Photos/Photos from 2022/IMG_1.HEIC", 2022),
        ("Takeout/Google Photos/Photos from 2026/IMG_1.HEIC", 2026),
        ("Takeout/Google Photos/Lake Como/IMG_1.HEIC", None),
        ("Takeout/Google Photos/Photos from nineteen/IMG_1.HEIC", None),
    ],
)
def test_year_from_path(path, year):
    assert takeout.year_from_path(path) == year


def test_live_photo_pairs_are_recognised():
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_1.MOV") is True
    assert takeout.is_live_photo_pair("IMG_1.MOV", "IMG_1.HEIC") is True
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_2.MOV") is False
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_1.JPG") is False
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_takeout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.takeout'`

- [x] **Step 3: Write the implementation**

Create `photolib/takeout.py`:

```python
"""Pure naming rules for Google Photos Takeout exports.

No I/O, no Drive, no database — every function here is a string transformation,
which is why they are cheap to test exhaustively.

Takeout names a photo's metadata sidecar after the photo, with two quirks that
break naive matching:

1. A duplicate index sits *outside* the extension. `IMG_7324(1).PNG` may be
   described by `IMG_7324.PNG.supplemental-metadata(1).json`.
2. Sidecar filenames are truncated to 51 characters, which can cut the
   `.supplemental-metadata` marker part-way through.
"""

from __future__ import annotations

import os
import re

JSON_SUFFIX = ".json"
MARKER = ".supplemental-metadata"
TRUNCATION_LIMIT = 51

STILL_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}

_INDEX = re.compile(r"\((\d+)\)$")
_YEAR_FOLDER = re.compile(r"^Photos from (\d{4})$")


def is_truncated(sidecar_name: str) -> bool:
    """True when Takeout cut this sidecar name at its length limit."""
    return len(sidecar_name) >= TRUNCATION_LIMIT


def _strip_marker(base: str) -> str:
    """Remove a trailing `.supplemental-metadata`, including a truncated one."""
    dot = base.rfind(".")
    if dot < 0:
        return base
    tail = base[dot:]
    # A truncated marker is any leading fragment of the full marker, but must be
    # long enough not to swallow a real extension like `.HEIC`.
    if len(tail) >= 4 and MARKER.startswith(tail.lower()):
        return base[:dot]
    return base


def media_name_for_sidecar(sidecar_name: str) -> str:
    """Return the media filename a sidecar describes.

    Raises ValueError if the name is not a sidecar.
    """
    if not sidecar_name.lower().endswith(JSON_SUFFIX):
        raise ValueError(f"not a sidecar: {sidecar_name}")

    base = sidecar_name[: -len(JSON_SUFFIX)]

    index = ""
    match = _INDEX.search(base)
    if match:
        index = match.group(0)
        base = base[: match.start()]

    base = _strip_marker(base)

    if not index:
        return base

    root, ext = os.path.splitext(base)
    return f"{root}{index}{ext}"


def stem(name: str) -> str:
    """Filename without its final extension, duplicate index preserved."""
    return os.path.splitext(name)[0]


def year_from_path(path: str) -> int | None:
    """The year of a `Photos from YYYY` folder anywhere in the path."""
    for part in path.split("/"):
        match = _YEAR_FOLDER.match(part)
        if match:
            return int(match.group(1))
    return None


def is_live_photo_pair(a: str, b: str) -> bool:
    """True when two names are the still and video halves of one capture."""
    if stem(a).lower() != stem(b).lower():
        return False
    ext_a = os.path.splitext(a)[1].lower()
    ext_b = os.path.splitext(b)[1].lower()
    return (ext_a in STILL_EXTENSIONS and ext_b in VIDEO_EXTENSIONS) or (
        ext_b in STILL_EXTENSIONS and ext_a in VIDEO_EXTENSIONS
    )
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_takeout.py -v`
Expected: 15 passed

- [x] **Step 5: Commit**

```bash
git add photolib/takeout.py tests/test_takeout.py
git commit -m "feat: takeout naming rules for sidecar matching"
```

---

## Task 3: Scan repository

**Files:**
- Create: `photolib/db/scan_repo.py`
- Create: `tests/test_scan_repo.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect`, `photolib.ziparchive.reader.ZipEntry`.
- Produces: `photolib.db.scan_repo.ScanRepo(conn)` with:
  - `upsert_archive(drive_id: str, name: str, size: int, modified_time: str | None) -> int` returning the archive row id.
  - `archive_is_current(drive_id: str, size: int, modified_time: str | None) -> bool`.
  - `replace_entries(archive_id: int, entries: list[ZipEntry], kinds: dict[str, str]) -> None` — `kinds` maps entry path to `'media'` or `'sidecar'`.
  - `mark_indexed(archive_id: int) -> None`.
  - `entries_of_kind(kind: str) -> list[sqlite3.Row]` — joined with the archive's `drive_id` and `name`.
  - `replace_drive_files(rows: list[dict]) -> None` — each dict has `drive_id`, `name`, `parent_path`, `md5`, `size`.
  - `drive_file_names() -> dict[str, list[sqlite3.Row]]` — name to the rows carrying it.
  - `counts() -> dict[str, int]` with keys `archives`, `entries`, `media`, `sidecars`, `drive_files`.

- [x] **Step 1: Write the failing test**

Create `tests/test_scan_repo.py`:

```python
from photolib.db.scan_repo import ScanRepo
from photolib.ziparchive.reader import ZipEntry


def entry(path: str, name: str, offset: int = 0) -> ZipEntry:
    return ZipEntry(
        path=path, name=name, crc32=1, size=10, compressed_size=5,
        method=8, local_header_offset=offset,
    )


def test_upsert_archive_returns_a_stable_id(conn):
    repo = ScanRepo(conn)
    first = repo.upsert_archive("z1", "takeout-001.zip", 100, "2026-01-01T00:00:00Z")
    again = repo.upsert_archive("z1", "takeout-001.zip", 100, "2026-01-01T00:00:00Z")
    assert first == again


def test_archive_is_current_tracks_size_and_mtime(conn):
    repo = ScanRepo(conn)
    repo.upsert_archive("z1", "a.zip", 100, "2026-01-01T00:00:00Z")
    repo.mark_indexed(repo.upsert_archive("z1", "a.zip", 100, "2026-01-01T00:00:00Z"))
    assert repo.archive_is_current("z1", 100, "2026-01-01T00:00:00Z") is True
    assert repo.archive_is_current("z1", 999, "2026-01-01T00:00:00Z") is False
    assert repo.archive_is_current("z1", 100, "2026-06-01T00:00:00Z") is False
    assert repo.archive_is_current("unknown", 100, None) is False


def test_archive_is_not_current_until_indexed(conn):
    repo = ScanRepo(conn)
    repo.upsert_archive("z1", "a.zip", 100, "t")
    assert repo.archive_is_current("z1", 100, "t") is False


def test_replace_entries_is_idempotent(conn):
    repo = ScanRepo(conn)
    aid = repo.upsert_archive("z1", "a.zip", 100, "t")
    entries = [entry("d/one.HEIC", "one.HEIC"), entry("d/one.HEIC.json", "one.HEIC.json", 50)]
    kinds = {"d/one.HEIC": "media", "d/one.HEIC.json": "sidecar"}
    repo.replace_entries(aid, entries, kinds)
    repo.replace_entries(aid, entries, kinds)
    assert repo.counts()["entries"] == 2
    assert repo.counts()["media"] == 1
    assert repo.counts()["sidecars"] == 1


def test_entries_of_kind_carries_the_archive_drive_id(conn):
    repo = ScanRepo(conn)
    aid = repo.upsert_archive("z1", "a.zip", 100, "t")
    repo.replace_entries(aid, [entry("d/one.HEIC", "one.HEIC")], {"d/one.HEIC": "media"})
    (row,) = repo.entries_of_kind("media")
    assert row["name"] == "one.HEIC"
    assert row["archive_drive_id"] == "z1"
    assert row["archive_name"] == "a.zip"
    assert row["local_header_offset"] == 0


def test_replace_drive_files_round_trips(conn):
    repo = ScanRepo(conn)
    repo.replace_drive_files([
        {"drive_id": "f1", "name": "IMG_1.HEIC", "parent_path": "back_2024_01",
         "md5": "abc", "size": 10},
        {"drive_id": "f2", "name": "IMG_1.HEIC", "parent_path": "back_2025_01",
         "md5": "def", "size": 20},
    ])
    by_name = repo.drive_file_names()
    assert len(by_name["IMG_1.HEIC"]) == 2
    assert {r["parent_path"] for r in by_name["IMG_1.HEIC"]} == {"back_2024_01", "back_2025_01"}


def test_replace_drive_files_clears_the_previous_index(conn):
    repo = ScanRepo(conn)
    repo.replace_drive_files([
        {"drive_id": "f1", "name": "old.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    repo.replace_drive_files([
        {"drive_id": "f2", "name": "new.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    assert "old.HEIC" not in repo.drive_file_names()
    assert repo.counts()["drive_files"] == 1
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scan_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.db.scan_repo'`

- [x] **Step 3: Write the implementation**

Create `photolib/db/scan_repo.py`:

```python
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

    def replace_drive_files(self, rows: list[dict]) -> None:
        """Replace the whole destination index; it is rebuilt on every scan."""
        self._conn.execute("DELETE FROM drive_files")
        stamp = _now()
        self._conn.executemany(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (r["drive_id"], r["name"], r["parent_path"], r["md5"], r["size"], stamp)
                for r in rows
            ],
        )
        self._conn.commit()

    def drive_file_names(self) -> dict[str, list[sqlite3.Row]]:
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in self._conn.execute("SELECT * FROM drive_files"):
            grouped[row["name"]].append(row)
        return grouped

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
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scan_repo.py -v`
Expected: 7 passed

- [x] **Step 5: Commit**

```bash
git add photolib/db/scan_repo.py tests/test_scan_repo.py
git commit -m "feat: scan repository for archive and destination indexes"
```

---

## Task 4: Scan Archives action

**Files:**
- Create: `photolib/actions/scan_archives.py`
- Create: `tests/test_action_scan.py`

**Interfaces:**
- Consumes: `ScanRepo`, `archives.list_archive_entries`, `archives.classify`, `ActionContext`, `ProgressEvent`.
- Produces: action `scan_archives` (ORDER 10). Indexes every `.zip` in `zip_source` into `archives`/`entries` and walks `photos_root` two levels deep into `drive_files`.

- [x] **Step 1: Write the failing test**

Create `tests/test_action_scan.py`:

```python
import pytest

from photolib.actions.base import ActionContext
from photolib.actions.scan_archives import Params, run
from photolib.config import Config
from photolib.db import catalog
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2022/IMG_1.HEIC": b"heic-bytes",
    "Takeout/Google Photos/Photos from 2022/IMG_1.HEIC.supplemental-metadata.json":
        b'{"title": "IMG_1.HEIC"}',
    "Takeout/Google Photos/Lake Como/IMG_2.MOV": b"mov-bytes",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)

    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(ARCHIVE), parent="zips")
    drive.add_folder("photos", "Photos")
    drive.add_folder("back", "back_2024_01", parent="photos")
    drive.add_file("d1", "IMG_1.HEIC", b"existing-bytes", parent="back")

    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    return ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)


def drain(ctx):
    return [event.message for event in run(ctx, Params())]


def test_indexes_every_entry_with_its_kind(ctx):
    drain(ctx)
    counts = ScanRepo(ctx.conn).counts()
    assert counts["archives"] == 1
    assert counts["media"] == 2
    assert counts["sidecars"] == 1


def test_indexes_the_destination_folder(ctx):
    drain(ctx)
    by_name = ScanRepo(ctx.conn).drive_file_names()
    assert by_name["IMG_1.HEIC"][0]["parent_path"] == "back_2024_01"


def test_rerun_skips_unchanged_archives(ctx):
    drain(ctx)
    messages = drain(ctx)
    assert any("unchanged" in m for m in messages)
    assert ScanRepo(ctx.conn).counts()["entries"] == 3


def test_reports_missing_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    ctx = ActionContext(
        conn=conn, drive=FakeDrive(), settings=SettingsRepo(conn), config=cfg
    )
    events = list(run(ctx, Params()))
    assert any(e.level == "error" for e in events)


def test_run_is_a_generator():
    import inspect

    assert inspect.isgeneratorfunction(run)
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_action_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.actions.scan_archives'`

- [x] **Step 3: Write the implementation**

Create `photolib/actions/scan_archives.py`:

```python
"""Index the ZIP archives and the destination folder into the catalog.

Reads only each archive's central directory over byte ranges — a few hundred
kilobytes for a 2.15 GB archive — and never downloads an archive whole.
"""

from __future__ import annotations

from typing import Iterator

from photolib import archives
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE
from photolib.drive.errors import DriveError

ID = "scan_archives"
TITLE = "Scan Archives"
DESCRIPTION = (
    "Index every ZIP archive's contents and the existing contents of the "
    "Global Photos folder. Reads only archive indexes, never whole archives."
)
ORDER = 10


class Params(ActionParams):
    pass


def _index_destination(ctx: ActionContext, folder_id: str) -> int:
    """Walk the destination two levels deep and return how many files were seen."""
    rows: list[dict] = []
    for child in ctx.drive.list_children(folder_id):
        if not child.is_folder:
            rows.append(
                {
                    "drive_id": child.id, "name": child.name, "parent_path": "",
                    "md5": child.md5, "size": child.size,
                }
            )
            continue
        for grandchild in ctx.drive.list_children(child.id):
            if grandchild.is_folder:
                continue
            rows.append(
                {
                    "drive_id": grandchild.id, "name": grandchild.name,
                    "parent_path": child.name, "md5": grandchild.md5,
                    "size": grandchild.size,
                }
            )
    ScanRepo(ctx.conn).replace_drive_files(rows)
    return len(rows)


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    repo = ScanRepo(ctx.conn)

    zip_source = ctx.settings.get_folder(ZIP_SOURCE)
    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if zip_source is None or photos_root is None:
        yield ProgressEvent(
            "Both the ZIP source and Global Photos folders must be configured "
            "in Settings before scanning.",
            progress=1.0,
            level="error",
        )
        return

    try:
        children = ctx.drive.list_children(zip_source.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the ZIP source folder: {exc}", progress=1.0, level="error"
        )
        return

    zips = sorted(
        (c for c in children if c.name.lower().endswith(".zip")), key=lambda f: f.name
    )
    if not zips:
        yield ProgressEvent(
            f"No archives found in '{zip_source.name}'.", progress=1.0, level="warn"
        )
        return

    total = len(zips) + 1
    yield ProgressEvent(f"Found {len(zips)} archive(s).", progress=0.0)

    for index, archive in enumerate(zips, start=1):
        progress = index / total
        if repo.archive_is_current(archive.id, archive.size, archive.modified_time):
            yield ProgressEvent(
                f"{archive.name}: unchanged since last scan, skipping.",
                progress=progress,
            )
            continue

        archive_id = repo.upsert_archive(
            archive.id, archive.name, archive.size, archive.modified_time
        )
        try:
            entries = archives.list_archive_entries(
                ctx.drive, archive.id, archive.size
            )
        except (DriveError, ValueError) as exc:
            yield ProgressEvent(
                f"{archive.name}: cannot read index — {exc}",
                progress=progress,
                level="error",
            )
            continue

        kinds = {e.path: archives.classify(e.path) for e in entries}
        repo.replace_entries(archive_id, entries, kinds)
        repo.mark_indexed(archive_id)

        media = sum(1 for k in kinds.values() if k == archives.MEDIA)
        yield ProgressEvent(
            f"{archive.name}: {len(entries)} entries "
            f"({media} media, {len(entries) - media} sidecars).",
            progress=progress,
        )

    seen = _index_destination(ctx, photos_root.id)
    counts = repo.counts()
    yield ProgressEvent(
        f"Indexed {seen} existing file(s) in '{photos_root.name}'.",
        progress=(total - 0.5) / total,
    )
    yield ProgressEvent(
        f"Scan complete: {counts['media']} media and {counts['sidecars']} "
        f"sidecars across {counts['archives']} archive(s).",
        progress=1.0,
    )
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_action_scan.py -v`
Expected: 5 passed

- [x] **Step 5: Confirm the action is discovered**

Run: `uv run python -c "from photolib.actions.registry import all_actions; print([a.id for a in all_actions()])"`
Expected: `['check_connection', 'scan_archives']`

- [x] **Step 6: Commit**

```bash
git add photolib/actions/scan_archives.py tests/test_action_scan.py
git commit -m "feat: scan archives action indexing archives and destination"
```

---

## Task 5: Media repository

**Files:**
- Create: `photolib/db/media_repo.py`
- Create: `tests/test_media_repo.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect`.
- Produces: `photolib.db.media_repo.MediaRepo(conn)` with:
  - `save_sidecar(entry_id: int, parsed: dict, raw_json: str) -> int`.
  - `upsert_media(entry_id: int, **fields) -> int` — creates or updates the row for an entry.
  - `link_sidecar(entry_id: int, sidecar_id: int) -> None`.
  - `set_plan(entry_id: int, **fields) -> None` — writes capture, place, target and duplicate columns.
  - `clear_plan() -> None` — resets planning columns on every media row, leaving `upload_status` and Drive results alone.
  - `all_media() -> list[sqlite3.Row]` — joined with entry path, name, size and archive name.
  - `summary() -> dict` — counts for the Review page.
  - `unpaired_sidecars() -> list[sqlite3.Row]`.

- [x] **Step 1: Write the failing test**

Create `tests/test_media_repo.py`:

```python
import json

import pytest

from photolib.db.media_repo import MediaRepo


@pytest.fixture
def seeded(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.executemany(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES (1,?,?,1,10,5,8,0,?)",
        [
            ("d/IMG_1.HEIC", "IMG_1.HEIC", "media"),
            ("d/IMG_1.HEIC.json", "IMG_1.HEIC.json", "sidecar"),
            ("d/IMG_2.MOV", "IMG_2.MOV", "media"),
        ],
    )
    conn.commit()
    return conn


def test_save_sidecar_stores_parsed_and_raw(seeded):
    repo = MediaRepo(seeded)
    sid = repo.save_sidecar(
        2,
        {"title": "IMG_1.HEIC", "photo_taken_time": 1700000000,
         "latitude": 52.2, "longitude": 21.0},
        json.dumps({"title": "IMG_1.HEIC"}),
    )
    row = seeded.execute("SELECT * FROM sidecars WHERE id = ?", (sid,)).fetchone()
    assert row["title"] == "IMG_1.HEIC"
    assert row["photo_taken_time"] == 1700000000
    assert json.loads(row["raw_json"])["title"] == "IMG_1.HEIC"


def test_save_sidecar_is_idempotent(seeded):
    repo = MediaRepo(seeded)
    first = repo.save_sidecar(2, {"title": "a"}, "{}")
    again = repo.save_sidecar(2, {"title": "b"}, "{}")
    assert first == again
    row = seeded.execute("SELECT title FROM sidecars WHERE id = ?", (first,)).fetchone()
    assert row["title"] == "b"


def test_upsert_media_is_idempotent(seeded):
    repo = MediaRepo(seeded)
    assert repo.upsert_media(1) == repo.upsert_media(1)
    assert len(repo.all_media()) == 1


def test_media_defaults_to_pending(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    assert repo.all_media()[0]["upload_status"] == "pending"


def test_set_plan_writes_every_column(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(
        1, capture_time=1700000000, capture_source="photo_taken_time",
        latitude=52.2, longitude=21.0, place="Warsaw", country="Poland",
        target_folder="2023-11", target_name="IMG_1.HEIC",
        duplicate_of="back_2024_01", duplicate_reason="name and size match",
    )
    row = repo.all_media()[0]
    assert row["target_folder"] == "2023-11"
    assert row["place"] == "Warsaw"
    assert row["duplicate_reason"] == "name and size match"
    assert row["upload_status"] == "pending"   # a verdict never withholds upload


def test_clear_plan_keeps_upload_results(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    seeded.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'x' WHERE entry_id = 1"
    )
    seeded.commit()
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["target_folder"] is None
    assert row["upload_status"] == "done"
    assert row["drive_file_id"] == "x"


def test_all_media_joins_entry_and_archive(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    row = repo.all_media()[0]
    assert row["name"] == "IMG_1.HEIC"
    assert row["path"] == "d/IMG_1.HEIC"
    assert row["archive_name"] == "a.zip"


def test_summary_counts(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.upsert_media(3)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC",
                  capture_source="photo_taken_time", place="Warsaw")
    repo.set_plan(3, duplicate_of="back_2024_01", duplicate_reason="name match")
    s = repo.summary()
    assert s["media"] == 2
    assert s["planned"] == 1
    assert s["duplicates"] == 1
    assert s["with_place"] == 1
    assert s["unplanned"] == 1


def test_unpaired_sidecars(seeded):
    repo = MediaRepo(seeded)
    assert [r["name"] for r in repo.unpaired_sidecars()] == ["IMG_1.HEIC.json"]
    repo.save_sidecar(2, {"title": "x"}, "{}")
    repo.upsert_media(1)
    repo.link_sidecar(1, 1)
    assert repo.unpaired_sidecars() == []
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_media_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.db.media_repo'`

- [x] **Step 3: Write the implementation**

Create `photolib/db/media_repo.py`:

```python
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
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media_repo.py -v`
Expected: 9 passed

- [x] **Step 5: Commit**

```bash
git add photolib/db/media_repo.py tests/test_media_repo.py
git commit -m "feat: media repository for sidecars and planning results"
```

---

## Task 6: Pair Metadata action

Resolves the export's hardest problem: 88% of sidecars sit in a different
archive part from the media they describe, so matching is global, not local.

**Files:**
- Create: `photolib/actions/pair_metadata.py`
- Create: `tests/test_action_pair.py`

**Interfaces:**
- Consumes: `ScanRepo`, `MediaRepo`, `takeout`, `archives.extract_from_archive`.
- Produces: action `pair_metadata` (ORDER 20), and `photolib.actions.pair_metadata.parse_sidecar(payload: dict) -> dict` returning the keys `title`, `photo_taken_time`, `creation_time`, `latitude`, `longitude`, `altitude`, `url`, `device`.

- [x] **Step 1: Write the failing test**

Create `tests/test_action_pair.py`:

```python
import json

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.pair_metadata import Params, parse_sidecar, run
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

SIDECAR = json.dumps({
    "title": "IMG_1.HEIC",
    "photoTakenTime": {"timestamp": "1700000000"},
    "creationTime": {"timestamp": "1700000500"},
    "geoData": {"latitude": 52.23, "longitude": 21.01, "altitude": 100.0},
    "url": "https://photos.google.com/x",
    "googlePhotosOrigin": {"mobileUpload": {"deviceType": "IOS_PHONE"}},
}).encode()

# The media is in part 1, its sidecar in part 2 — the 88% case.
PART_1 = {"Takeout/Google Photos/Photos from 2023/IMG_1.HEIC": b"heic"}
PART_2 = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC.supplemental-metadata.json":
        SIDECAR,
    "Takeout/Google Photos/Photos from 2023/IMG_9.MOV.supplemental-metadata.json":
        b'{"title": "IMG_9.MOV"}',
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(PART_1), parent="zips")
    drive.add_file("z2", "takeout-002.zip", build_zip(PART_2), parent="zips")
    drive.add_folder("photos", "Photos")
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    list(scan(context, ScanParams()))
    return context


def test_parse_sidecar_flattens_google_shapes():
    parsed = parse_sidecar(json.loads(SIDECAR))
    assert parsed["title"] == "IMG_1.HEIC"
    assert parsed["photo_taken_time"] == 1700000000
    assert parsed["creation_time"] == 1700000500
    assert parsed["latitude"] == 52.23
    assert parsed["device"] == "IOS_PHONE"


def test_parse_sidecar_treats_zero_coordinates_as_absent():
    parsed = parse_sidecar({"title": "x", "geoData": {"latitude": 0.0, "longitude": 0.0}})
    assert parsed["latitude"] is None
    assert parsed["longitude"] is None


def test_parse_sidecar_survives_missing_fields():
    parsed = parse_sidecar({"title": "x"})
    assert parsed["title"] == "x"
    assert parsed["photo_taken_time"] is None


def test_pairs_across_archive_parts(ctx):
    list(run(ctx, Params()))
    (row,) = [m for m in MediaRepo(ctx.conn).all_media() if m["name"] == "IMG_1.HEIC"]
    assert row["sidecar_id"] is not None


def test_creates_a_media_row_for_every_media_entry(ctx):
    list(run(ctx, Params()))
    assert len(MediaRepo(ctx.conn).all_media()) == 1


def test_reports_sidecars_with_no_media(ctx):
    messages = [e.message for e in run(ctx, Params())]
    assert any("1 sidecar" in m and "no media" in m for m in messages)


def test_rerun_is_idempotent(ctx):
    list(run(ctx, Params()))
    list(run(ctx, Params()))
    assert len(MediaRepo(ctx.conn).all_media()) == 1
    # Both sidecars are stored, including the orphan describing IMG_9.MOV — its
    # parsed data is kept for diagnosis. Re-running must not duplicate either.
    assert ctx.conn.execute("SELECT COUNT(*) FROM sidecars").fetchone()[0] == 2
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_action_pair.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.actions.pair_metadata'`

- [x] **Step 3: Write the implementation**

Create `photolib/actions/pair_metadata.py`:

```python
"""Extract every sidecar and match it to the media file it describes.

Matching is global rather than per-archive: measurements show 88% of sidecars
live in a different archive part from their media, so a per-archive pass would
fail for the overwhelming majority of the export.
"""

from __future__ import annotations

import json
from typing import Iterator

from photolib import archives, takeout
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo
from photolib.ziparchive.reader import CorruptEntryError, ZipEntry

ID = "pair_metadata"
TITLE = "Pair Metadata"
DESCRIPTION = (
    "Read every metadata sidecar and match it to its photo or video, including "
    "the majority whose sidecar sits in a different archive part."
)
ORDER = 20


class Params(ActionParams):
    pass


def _timestamp(payload: dict, key: str) -> int | None:
    raw = (payload.get(key) or {}).get("timestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_sidecar(payload: dict) -> dict:
    """Flatten Google's nested sidecar shape into flat catalog columns."""
    geo = payload.get("geoData") or {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    # Google writes 0.0/0.0 when a photo carries no location.
    if not lat and not lon:
        lat = lon = None

    origin = payload.get("googlePhotosOrigin") or {}
    device = (origin.get("mobileUpload") or {}).get("deviceType")

    return {
        "title": payload.get("title"),
        "photo_taken_time": _timestamp(payload, "photoTakenTime"),
        "creation_time": _timestamp(payload, "creationTime"),
        "latitude": lat,
        "longitude": lon,
        "altitude": geo.get("altitude"),
        "url": payload.get("url"),
        "device": device,
    }


def _entry_for(row) -> ZipEntry:
    return ZipEntry(
        path=row["path"], name=row["name"], crc32=row["crc32"], size=row["size"],
        compressed_size=row["compressed_size"], method=row["method"],
        local_header_offset=row["local_header_offset"],
    )


def _match(candidate: str, by_name: dict, by_prefix: list[tuple[str, object]]):
    """Exact name first, then truncated-prefix — the two Takeout naming quirks."""
    if candidate in by_name:
        return by_name[candidate]
    for name, row in by_prefix:
        if name.startswith(candidate):
            return row
    return None


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    scan_repo = ScanRepo(ctx.conn)
    media_repo = MediaRepo(ctx.conn)

    media_rows = scan_repo.entries_of_kind("media")
    sidecar_rows = scan_repo.entries_of_kind("sidecar")
    if not media_rows:
        yield ProgressEvent(
            "No indexed media. Run Scan Archives first.", progress=1.0, level="error"
        )
        return

    for row in media_rows:
        media_repo.upsert_media(row["id"])
    yield ProgressEvent(f"{len(media_rows)} media file(s) catalogued.", progress=0.05)

    by_name = {row["name"]: row for row in media_rows}
    by_prefix = sorted((row["name"], row) for row in media_rows)

    paired = orphaned = unreadable = 0
    total = max(len(sidecar_rows), 1)

    for index, row in enumerate(sidecar_rows, start=1):
        if index % 50 == 0 or index == len(sidecar_rows):
            yield ProgressEvent(
                f"Paired {paired} of {index} sidecar(s).",
                progress=0.05 + 0.9 * index / total,
            )

        try:
            payload = json.loads(
                archives.extract_from_archive(
                    ctx.drive, row["archive_drive_id"], _entry_for(row)
                )
            )
        except (CorruptEntryError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            unreadable += 1
            yield ProgressEvent(
                f"{row['name']}: unreadable sidecar — {exc}", level="warn"
            )
            continue

        parsed = parse_sidecar(payload)
        sidecar_id = media_repo.save_sidecar(
            row["id"], parsed, json.dumps(payload, ensure_ascii=False)
        )

        candidate = takeout.media_name_for_sidecar(row["name"])
        match = _match(candidate, by_name, by_prefix)
        if match is None and parsed["title"]:
            match = _match(parsed["title"], by_name, by_prefix)

        if match is None:
            orphaned += 1
            continue

        media_repo.link_sidecar(match["id"], sidecar_id)
        paired += 1

    detail = f"Paired {paired} sidecar(s) to media."
    if orphaned:
        detail += f" {orphaned} sidecar(s) matched no media."
    if unreadable:
        detail += f" {unreadable} sidecar(s) were unreadable."
    yield ProgressEvent(detail, progress=1.0, level="warn" if orphaned else "info")
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_action_pair.py -v`
Expected: 7 passed

- [x] **Step 5: Commit**

```bash
git add photolib/actions/pair_metadata.py tests/test_action_pair.py
git commit -m "feat: pair metadata action matching sidecars across archive parts"
```

---

## Task 7: Places and geocoding

**Files:**
- Create: `photolib/places.py`
- Create: `tests/test_places.py`

**Interfaces:**
- Consumes: `httpx`, the catalog connection.
- Produces:
  - `photolib.places.cache_key(lat: float, lon: float) -> str` — coordinates rounded to two decimals, roughly 1 km.
  - `photolib.places.Geocoder(conn, api_key: str | None, http=None)` with `lookup(lat: float, lon: float) -> tuple[str | None, str | None]` returning `(place, country)`, consulting the `geocache` table first and returning `(None, None)` when no key is configured.
  - `photolib.places.api_key_from_env() -> str | None` — reads `GOOGLE_MAPS_API_KEY`, falling back to a `.env` file at the repo root.

- [x] **Step 1: Write the failing test**

Create `tests/test_places.py`:

```python
import httpx

from photolib.places import Geocoder, api_key_from_env, cache_key

RESPONSE = {
    "status": "OK",
    "results": [
        {
            "address_components": [
                {"long_name": "Warsaw", "types": ["locality", "political"]},
                {"long_name": "Poland", "types": ["country", "political"]},
            ]
        }
    ],
}


def geocoder_with(conn, handler, api_key="test-key"):
    return Geocoder(
        conn, api_key, http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_cache_key_rounds_to_about_one_kilometre():
    assert cache_key(52.2312345, 21.0119999) == "52.23,21.01"
    assert cache_key(52.2312345, 21.0119999) == cache_key(52.2349, 21.0121)


def test_lookup_returns_place_and_country(conn):
    geo = geocoder_with(conn, lambda request: httpx.Response(200, json=RESPONSE))
    assert geo.lookup(52.23, 21.01) == ("Warsaw", "Poland")


def test_lookup_caches_and_does_not_call_twice(conn):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=RESPONSE)

    geo = geocoder_with(conn, handler)
    geo.lookup(52.23, 21.01)
    geo.lookup(52.2349, 21.0121)   # same rounded key
    assert calls["n"] == 1


def test_cache_survives_a_new_geocoder(conn):
    geocoder_with(conn, lambda r: httpx.Response(200, json=RESPONSE)).lookup(52.23, 21.01)

    def explode(request):
        raise AssertionError("should have been served from cache")

    assert geocoder_with(conn, explode).lookup(52.23, 21.01) == ("Warsaw", "Poland")


def test_no_api_key_returns_nothing_and_makes_no_call(conn):
    def explode(request):
        raise AssertionError("must not call the API without a key")

    geo = geocoder_with(conn, explode, api_key=None)
    assert geo.lookup(52.23, 21.01) == (None, None)


def test_zero_results_is_cached_as_empty(conn):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})

    geo = geocoder_with(conn, handler)
    assert geo.lookup(1.0, 1.0) == (None, None)
    geo.lookup(1.0, 1.0)
    assert calls["n"] == 1


def test_api_failure_returns_nothing_without_raising(conn):
    geo = geocoder_with(conn, lambda r: httpx.Response(500, text="boom"))
    assert geo.lookup(52.23, 21.01) == (None, None)


def test_api_key_from_env_prefers_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "from-env")
    assert api_key_from_env(tmp_path) == "from-env"


def test_api_key_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    (tmp_path / ".env").write_text("# a comment\nGOOGLE_MAPS_API_KEY=from-file\n")
    assert api_key_from_env(tmp_path) == "from-file"


def test_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert api_key_from_env(tmp_path) is None
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_places.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.places'`

- [x] **Step 3: Write the implementation**

Create `photolib/places.py`:

```python
"""Reverse-geocoding with a persistent cache.

Coordinates cluster heavily in this library, so rounding to roughly a kilometre
before caching turns hundreds of files into a handful of API calls. The API key
is optional: with none configured, place lookup degrades to no-op rather than
failing the surrounding work.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ENV_VAR = "GOOGLE_MAPS_API_KEY"
PLACE_TYPES = ("locality", "postal_town", "administrative_area_level_2")


def cache_key(lat: float, lon: float) -> str:
    """Round to ~1 km so nearby photos share one cache entry."""
    return f"{lat:.2f},{lon:.2f}"


def api_key_from_env(repo_root: Path | None = None) -> str | None:
    """The Geocoding API key from the environment, or from a `.env` file."""
    import os

    key = os.environ.get(ENV_VAR)
    if key:
        return key
    if repo_root is None:
        return None
    env_file = Path(repo_root) / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == ENV_VAR:
            return value.strip().strip("'\"") or None
    return None


class Geocoder:
    def __init__(self, conn: sqlite3.Connection, api_key: str | None, http=None) -> None:
        self._conn = conn
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def lookup(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        key = cache_key(lat, lon)
        cached = self._conn.execute(
            "SELECT place, country FROM geocache WHERE key = ?", (key,)
        ).fetchone()
        if cached is not None:
            return cached["place"], cached["country"]

        if not self._api_key:
            return None, None

        try:
            response = self._http.get(
                GEOCODE_URL,
                params={"latlng": f"{lat},{lon}", "key": self._api_key},
            )
            if not response.is_success:
                return None, None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None, None

        place, country = self._extract(payload)
        self._store(key, place, country, payload)
        return place, country

    @staticmethod
    def _extract(payload: dict) -> tuple[str | None, str | None]:
        place = country = None
        for result in payload.get("results", []):
            for component in result.get("address_components", []):
                types = component.get("types", [])
                if country is None and "country" in types:
                    country = component.get("long_name")
                if place is None and any(t in types for t in PLACE_TYPES):
                    place = component.get("long_name")
            if place and country:
                break
        return place, country

    def _store(self, key: str, place, country, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO geocache (key, place, country, raw_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET place = excluded.place, "
            "country = excluded.country, raw_json = excluded.raw_json",
            (key, place, country, json.dumps(payload)),
        )
        self._conn.commit()
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_places.py -v`
Expected: 10 passed

- [x] **Step 5: Commit**

```bash
git add photolib/places.py tests/test_places.py
git commit -m "feat: reverse geocoding with a persistent coordinate cache"
```

---

## Task 8: Plan Organization action

The stage that decides everything and changes nothing. Writes no bytes to Drive
and is re-runnable at will.

**Files:**
- Create: `photolib/actions/plan_organize.py`
- Create: `tests/test_action_plan.py`

**Interfaces:**
- Consumes: `MediaRepo`, `ScanRepo`, `places.Geocoder`, `takeout.year_from_path`.
- Produces: action `plan_organize` (ORDER 30) and `photolib.actions.plan_organize.resolve_capture(row, sidecar, archive_modified) -> tuple[int | None, str]` returning `(unix_seconds, source)` where source is one of `photo_taken_time`, `creation_time`, `year_folder`, `archive_mtime`, `unknown`.

- [x] **Step 1: Write the failing test**

Create `tests/test_action_plan.py`:

```python
import json

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.pair_metadata import Params as PairParams
from photolib.actions.pair_metadata import run as pair
from photolib.actions.plan_organize import Params, resolve_capture, run
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

# 1700000000 == 2023-11-14 UTC
SIDECAR = json.dumps({
    "title": "IMG_1.HEIC",
    "photoTakenTime": {"timestamp": "1700000000"},
    "geoData": {"latitude": 52.23, "longitude": 21.01},
}).encode()

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC": b"heic",
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC.supplemental-metadata.json":
        SIDECAR,
    # no sidecar: falls back to the year folder
    "Takeout/Google Photos/Photos from 2019/IMG_2.MOV": b"mov",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(ARCHIVE), parent="zips")
    drive.add_folder("photos", "Photos")
    drive.add_folder("back", "back_2024_01", parent="photos")
    drive.add_file("d1", "IMG_1.HEIC", b"heic", parent="back")
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    list(scan(context, ScanParams()))
    list(pair(context, PairParams()))
    return context


def by_name(ctx) -> dict:
    return {row["name"]: row for row in MediaRepo(ctx.conn).all_media()}


def test_resolve_capture_prefers_the_sidecar():
    when, source = resolve_capture(
        {"path": "Takeout/Google Photos/Photos from 2019/x.HEIC"},
        {"photo_taken_time": 1700000000, "creation_time": 1},
        None,
    )
    assert (when, source) == (1700000000, "photo_taken_time")


def test_resolve_capture_falls_back_to_the_year_folder():
    when, source = resolve_capture(
        {"path": "Takeout/Google Photos/Photos from 2019/x.HEIC"}, None, None
    )
    assert source == "year_folder"
    assert when is not None


def test_resolve_capture_gives_up_cleanly():
    when, source = resolve_capture({"path": "Takeout/Google Photos/Album/x.HEIC"}, None, None)
    assert (when, source) == (None, "unknown")


def test_target_folder_is_the_capture_month(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["target_folder"] == "2023-11"


def test_year_folder_fallback_is_recorded(ctx):
    list(run(ctx, Params()))
    row = by_name(ctx)["IMG_2.MOV"]
    assert row["capture_source"] == "year_folder"
    assert row["target_folder"] == "2019-01"


def test_duplicates_are_recorded_but_still_pending(ctx):
    list(run(ctx, Params()))
    row = by_name(ctx)["IMG_1.HEIC"]
    assert row["duplicate_of"] == "back_2024_01"
    assert "name" in row["duplicate_reason"]
    assert row["upload_status"] == "pending"      # never withheld


def test_no_media_is_ever_marked_skipped(ctx):
    list(run(ctx, Params()))
    statuses = {r["upload_status"] for r in MediaRepo(ctx.conn).all_media()}
    assert statuses == {"pending"}


def test_place_is_absent_without_an_api_key(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["place"] is None


def test_rerun_replaces_the_previous_plan(ctx):
    list(run(ctx, Params()))
    list(run(ctx, Params()))
    rows = MediaRepo(ctx.conn).all_media()
    assert len(rows) == 2
    assert all(r["target_name"] for r in rows)


def test_name_collisions_within_a_month_are_disambiguated(ctx):
    conn = ctx.conn
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z9', 'extra.zip', 1)"
    )
    # A second IMG_2.MOV in the same year folder. Neither copy has a sidecar, so
    # both resolve to 2019-01 and genuinely collide. (A second IMG_1.HEIC would
    # not: the original has a sidecar dating it to 2023-11 while the copy would
    # fall back to 2023-01, so the two would never share a folder.)
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES "
        "((SELECT id FROM archives WHERE drive_id='z9'),"
        " 'Takeout/Google Photos/Photos from 2019/IMG_2.MOV','IMG_2.MOV',"
        " 999,10,5,8,0,'media')"
    )
    conn.commit()
    # Pair Metadata is what normally creates media rows; this entry bypasses it.
    MediaRepo(conn).upsert_media(
        conn.execute("SELECT id FROM entries WHERE crc32 = 999").fetchone()["id"]
    )
    list(run(ctx, Params()))
    targets = [
        r["target_name"] for r in MediaRepo(ctx.conn).all_media()
        if r["name"] == "IMG_2.MOV"
    ]
    assert len(targets) == 2
    assert len(set(targets)) == 2, "colliding targets must be disambiguated"
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_action_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.actions.plan_organize'`

- [x] **Step 3: Write the implementation**

Create `photolib/actions/plan_organize.py`:

```python
"""Decide where every media file goes, without moving anything.

Writes nothing to Drive. Re-running replaces the previous plan, so it is safe to
run repeatedly while tuning.

Duplicate verdicts are recorded for information only. Per the operator's
decision they never withhold a file from upload — every media row stays
`pending`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterator

from photolib import places, takeout
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

ID = "plan_organize"
TITLE = "Plan Organization"
DESCRIPTION = (
    "Resolve a capture date, place, duplicate verdict and destination for every "
    "media file. Writes nothing to Drive and can be re-run at will."
)
ORDER = 30


class Params(ActionParams):
    pass


def resolve_capture(row, sidecar, archive_modified: str | None) -> tuple[int | None, str]:
    """Best available capture time, and which source supplied it."""
    if sidecar:
        if sidecar["photo_taken_time"]:
            return sidecar["photo_taken_time"], "photo_taken_time"
        if sidecar["creation_time"]:
            return sidecar["creation_time"], "creation_time"

    year = takeout.year_from_path(row["path"])
    if year is not None:
        stamp = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        return int(stamp), "year_folder"

    if archive_modified:
        try:
            parsed = datetime.fromisoformat(archive_modified.replace("Z", "+00:00"))
            return int(parsed.timestamp()), "archive_mtime"
        except ValueError:
            pass

    return None, "unknown"


def _month(capture: int | None) -> str:
    if capture is None:
        return "unknown-date"
    return datetime.fromtimestamp(capture, tz=timezone.utc).strftime("%Y-%m")


def _disambiguate(name: str, crc: int) -> str:
    root, ext = os.path.splitext(name)
    return f"{root}~{crc & 0xFFFFFF:06x}{ext}"


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    media_repo = MediaRepo(ctx.conn)
    scan_repo = ScanRepo(ctx.conn)

    rows = media_repo.all_media()
    if not rows:
        yield ProgressEvent(
            "No media catalogued. Run Scan Archives and Pair Metadata first.",
            progress=1.0,
            level="error",
        )
        return

    media_repo.clear_plan()
    rows = media_repo.all_media()

    existing = scan_repo.drive_file_names()
    geocoder = places.Geocoder(
        ctx.conn, places.api_key_from_env(ctx.config.repo_root)
    )
    if not geocoder.enabled:
        yield ProgressEvent(
            "No GOOGLE_MAPS_API_KEY configured — place tags will be skipped.",
            progress=0.0,
            level="warn",
        )

    taken: set[tuple[str, str]] = set()
    total = len(rows)
    duplicates = placed = unknown_dates = 0

    for index, row in enumerate(rows, start=1):
        if index % 100 == 0 or index == total:
            yield ProgressEvent(f"Planned {index} of {total}.", progress=index / total)

        sidecar = None
        if row["sidecar_id"]:
            sidecar = ctx.conn.execute(
                "SELECT * FROM sidecars WHERE id = ?", (row["sidecar_id"],)
            ).fetchone()

        archive_modified = ctx.conn.execute(
            "SELECT modified_time FROM archives WHERE drive_id = ?",
            (row["archive_drive_id"],),
        ).fetchone()["modified_time"]

        capture, source = resolve_capture(row, sidecar, archive_modified)
        if source == "unknown":
            unknown_dates += 1

        folder = _month(capture)
        name = row["name"]
        if (folder, name) in taken:
            name = _disambiguate(name, row["crc32"])
        taken.add((folder, name))

        lat = sidecar["latitude"] if sidecar else None
        lon = sidecar["longitude"] if sidecar else None
        place = country = None
        if lat is not None and lon is not None:
            place, country = geocoder.lookup(lat, lon)
            if place:
                placed += 1

        duplicate_of = duplicate_reason = None
        for candidate in existing.get(row["name"], []):
            if candidate["size"] == row["entry_size"]:
                duplicate_of = candidate["parent_path"]
                duplicate_reason = "name and size match an existing file"
                break
            duplicate_of = candidate["parent_path"]
            duplicate_reason = "name matches an existing file, size differs"
        if duplicate_of:
            duplicates += 1

        media_repo.set_plan(
            row["entry_id"],
            capture_time=capture,
            capture_source=source,
            latitude=lat,
            longitude=lon,
            place=place,
            country=country,
            target_folder=folder,
            target_name=name,
            duplicate_of=duplicate_of,
            duplicate_reason=duplicate_reason,
        )

    detail = f"Planned {total} file(s)."
    if duplicates:
        detail += f" {duplicates} already exist in the destination (will still upload)."
    if unknown_dates:
        detail += f" {unknown_dates} have no resolvable date."
    if placed:
        detail += f" {placed} carry a place."
    yield ProgressEvent(detail, progress=1.0)
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_action_plan.py -v`
Expected: 10 passed

- [x] **Step 5: Run the whole backend suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add photolib/actions/plan_organize.py tests/test_action_plan.py
git commit -m "feat: plan organization action resolving dates, places, and targets"
```

---

## Task 9: Review API routes

**Files:**
- Create: `photolib/api/routes_review.py`
- Modify: `photolib/api/app.py` — add one import and one `include_router` call
- Create: `tests/test_api_review.py`

**Interfaces:**
- Consumes: `MediaRepo`, `ScanRepo`.
- Produces:
  - `GET /api/review/summary` → `{"media", "planned", "unplanned", "duplicates", "with_place", "with_sidecar", "pending", "archives", "entries", "drive_files"}`.
  - `GET /api/review/media?limit=&offset=&folder=&duplicates_only=` → `{"total": int, "rows": [...]}`. Each row carries `name`, `path`, `archive_name`, `target_folder`, `target_name`, `capture_time`, `capture_source`, `place`, `country`, `duplicate_of`, `duplicate_reason`, `upload_status`, `size`.

- [x] **Step 1: Write the failing test**

Create `tests/test_api_review.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from photolib.db.media_repo import MediaRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
        conn = app.state.conn
        conn.execute(
            "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
        )
        conn.executemany(
            "INSERT INTO entries (archive_id, path, name, crc32, size,"
            " compressed_size, method, local_header_offset, kind)"
            " VALUES (1,?,?,1,10,5,8,0,'media')",
            [("d/IMG_1.HEIC", "IMG_1.HEIC"), ("d/IMG_2.MOV", "IMG_2.MOV")],
        )
        conn.commit()
        repo = MediaRepo(conn)
        repo.upsert_media(1)
        repo.upsert_media(2)
        repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC",
                      capture_source="photo_taken_time", place="Warsaw")
        repo.set_plan(2, target_folder="2019-01", target_name="IMG_2.MOV",
                      capture_source="year_folder",
                      duplicate_of="back_2024_01", duplicate_reason="name match")
        yield c


def test_summary_reports_totals(client):
    body = client.get("/api/review/summary").json()
    assert body["media"] == 2
    assert body["planned"] == 2
    assert body["duplicates"] == 1
    assert body["with_place"] == 1


def test_media_returns_every_row(client):
    body = client.get("/api/review/media").json()
    assert body["total"] == 2
    assert {r["name"] for r in body["rows"]} == {"IMG_1.HEIC", "IMG_2.MOV"}


def test_media_pagination(client):
    body = client.get("/api/review/media", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 2
    assert len(body["rows"]) == 1


def test_media_filters_by_folder(client):
    body = client.get("/api/review/media", params={"folder": "2019-01"}).json()
    assert [r["name"] for r in body["rows"]] == ["IMG_2.MOV"]
    assert body["total"] == 1


def test_media_filters_duplicates_only(client):
    body = client.get("/api/review/media", params={"duplicates_only": "true"}).json()
    assert [r["name"] for r in body["rows"]] == ["IMG_2.MOV"]


def test_rows_never_report_skipped(client):
    body = client.get("/api/review/media").json()
    assert {r["upload_status"] for r in body["rows"]} == {"pending"}
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_review.py -v`
Expected: FAIL — the `/api/review/summary` route returns 404.

- [x] **Step 3: Write the routes**

Create `photolib/api/routes_review.py`:

```python
"""Read-only endpoints backing the Review page."""

from __future__ import annotations

from fastapi import APIRouter, Request

from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

router = APIRouter(tags=["review"])

ROW_FIELDS = (
    "name", "path", "archive_name", "target_folder", "target_name",
    "capture_time", "capture_source", "place", "country",
    "duplicate_of", "duplicate_reason", "upload_status",
)


@router.get("/review/summary")
def summary(request: Request) -> dict:
    conn = request.app.state.conn
    return {**MediaRepo(conn).summary(), **ScanRepo(conn).counts()}


@router.get("/review/media")
def media(
    request: Request,
    limit: int = 200,
    offset: int = 0,
    folder: str | None = None,
    duplicates_only: bool = False,
) -> dict:
    conn = request.app.state.conn

    where, args = [], []
    if folder:
        where.append("m.target_folder = ?")
        args.append(folder)
    if duplicates_only:
        where.append("m.duplicate_of IS NOT NULL")
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM media m {clause}", args
    ).fetchone()[0]

    rows = conn.execute(
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

    return {
        "total": total,
        "rows": [
            {**{f: row[f] for f in ROW_FIELDS}, "size": row["entry_size"]}
            for row in rows
        ],
    }
```

- [x] **Step 4: Wire the router into the app**

In `photolib/api/app.py`, extend the deferred import and add one include. The
import line becomes:

```python
    from photolib.api import (
        routes_actions,
        routes_drive,
        routes_jobs,
        routes_review,
        routes_settings,
    )
```

And add this line after the existing `include_router` calls, before `return app`:

```python
    app.include_router(routes_review.router, prefix="/api")
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_review.py -v`
Expected: 6 passed

- [x] **Step 6: Commit**

```bash
git add photolib/api/routes_review.py photolib/api/app.py tests/test_api_review.py
git commit -m "feat: review api exposing the plan and its summary"
```

---

## Task 10: Review page

**Files:**
- Modify: `web/src/api/types.ts` — add `ReviewSummary` and `ReviewMedia`
- Modify: `web/src/api/client.ts` — add `getReviewSummary` and `listReviewMedia`
- Create: `web/src/pages/ReviewPage.tsx`
- Modify: `web/src/App.tsx` — add the `/review` route
- Modify: `web/src/components/Nav.tsx` — add the Review link
- Create: `web/src/pages/ReviewPage.test.tsx`

**Interfaces:**
- Consumes: `/api/review/summary`, `/api/review/media`.
- Produces: `ReviewPage()` — summary tiles, a folder filter, a duplicates-only toggle, and a table of every planned file.

- [x] **Step 1: Write the failing test**

Create `web/src/pages/ReviewPage.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReviewPage } from './ReviewPage'

const listReviewMedia = vi.fn(async (_opts?: object) => ({
  total: 2,
  rows: [
    {
      name: 'IMG_1.HEIC', path: 'd/IMG_1.HEIC', archive_name: 'a.zip',
      target_folder: '2023-11', target_name: 'IMG_1.HEIC',
      capture_time: 1700000000, capture_source: 'photo_taken_time',
      place: 'Warsaw', country: 'Poland',
      duplicate_of: null, duplicate_reason: null,
      upload_status: 'pending', size: 100,
    },
    {
      name: 'IMG_2.MOV', path: 'd/IMG_2.MOV', archive_name: 'a.zip',
      target_folder: '2019-01', target_name: 'IMG_2.MOV',
      capture_time: null, capture_source: 'year_folder',
      place: null, country: null,
      duplicate_of: 'back_2024_01', duplicate_reason: 'name match',
      upload_status: 'pending', size: 200,
    },
  ],
}))

vi.mock('../api/client', () => ({
  getReviewSummary: vi.fn(async () => ({
    media: 2, planned: 2, unplanned: 0, duplicates: 1,
    with_place: 1, with_sidecar: 1, pending: 2,
    archives: 1, entries: 3, drive_files: 5,
  })),
  listReviewMedia: (opts?: object) => listReviewMedia(opts),
}))

afterEach(() => vi.clearAllMocks())

describe('ReviewPage', () => {
  it('shows the summary totals', async () => {
    render(<ReviewPage />)
    // Several tiles legitimately show the same number — media and planned are
    // both 2 once everything is planned — so assert on the tile as a unit
    // rather than on a bare value that matches more than one element.
    const label = await screen.findByText(/media files/i)
    expect(label.closest('.card')?.textContent).toContain('2')
  })

  it('lists every planned file with its destination', async () => {
    render(<ReviewPage />)
    expect(await screen.findByText('IMG_1.HEIC')).toBeTruthy()
    expect(screen.getByText('2023-11')).toBeTruthy()
    expect(screen.getByText('2019-01')).toBeTruthy()
  })

  it('flags a file that already exists in the destination', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_2.MOV')
    expect(screen.getByText(/back_2024_01/)).toBeTruthy()
  })

  it('says plainly that duplicates still upload', async () => {
    render(<ReviewPage />)
    expect(await screen.findByText(/still be uploaded/i)).toBeTruthy()
  })

  it('requests only duplicates when the toggle is used', async () => {
    render(<ReviewPage />)
    await screen.findByText('IMG_1.HEIC')
    await userEvent.click(screen.getByLabelText(/only files already in the destination/i))
    await waitFor(() =>
      expect(listReviewMedia).toHaveBeenLastCalledWith(
        expect.objectContaining({ duplicatesOnly: true }),
      ),
    )
  })
})
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./ReviewPage`

- [x] **Step 3: Add the types**

Append to `web/src/api/types.ts`:

```ts
export interface ReviewSummary {
  media: number
  planned: number
  unplanned: number
  duplicates: number
  with_place: number
  with_sidecar: number
  pending: number
  archives: number
  entries: number
  drive_files: number
}

export interface ReviewMedia {
  name: string
  path: string
  archive_name: string
  target_folder: string | null
  target_name: string | null
  capture_time: number | null
  capture_source: string | null
  place: string | null
  country: string | null
  duplicate_of: string | null
  duplicate_reason: string | null
  upload_status: string
  size: number
}
```

- [x] **Step 4: Add the client functions**

Append to `web/src/api/client.ts`, and extend the existing `import type` line at
the top of the file to also import `ReviewMedia` and `ReviewSummary`:

```ts
export const getReviewSummary = () => request<ReviewSummary>('/api/review/summary')

export const listReviewMedia = (opts: {
  limit?: number
  offset?: number
  folder?: string
  duplicatesOnly?: boolean
} = {}) => {
  const params = new URLSearchParams()
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.offset !== undefined) params.set('offset', String(opts.offset))
  if (opts.folder) params.set('folder', opts.folder)
  if (opts.duplicatesOnly) params.set('duplicates_only', 'true')
  return request<{ total: number; rows: ReviewMedia[] }>(
    `/api/review/media?${params.toString()}`,
  )
}
```

- [x] **Step 5: Write the page**

Create `web/src/pages/ReviewPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getReviewSummary, listReviewMedia } from '../api/client'
import type { ReviewMedia, ReviewSummary } from '../api/types'

const TILES: Array<{ key: keyof ReviewSummary; label: string }> = [
  { key: 'media', label: 'media files' },
  { key: 'planned', label: 'with a destination' },
  { key: 'duplicates', label: 'already in the destination' },
  { key: 'with_place', label: 'with a place' },
]

function when(row: ReviewMedia): string {
  if (row.capture_time === null) return '—'
  return new Date(row.capture_time * 1000).toISOString().slice(0, 10)
}

export function ReviewPage() {
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [rows, setRows] = useState<ReviewMedia[]>([])
  const [total, setTotal] = useState(0)
  const [duplicatesOnly, setDuplicatesOnly] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getReviewSummary().then(setSummary).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    listReviewMedia({ limit: 500, duplicatesOnly })
      .then((result) => {
        setRows(result.rows)
        setTotal(result.total)
      })
      .catch((e) => setError(String(e)))
  }, [duplicatesOnly])

  return (
    <>
      <h2>Review Plan</h2>
      {error && <p className="error">{error}</p>}

      {summary && (
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          {TILES.map((tile) => (
            <div className="card" key={tile.key} style={{ minWidth: '10rem' }}>
              <div style={{ fontSize: '1.6rem', fontVariantNumeric: 'tabular-nums' }}>
                {summary[tile.key]}
              </div>
              <div className="muted">{tile.label}</div>
            </div>
          ))}
        </div>
      )}

      {summary && summary.duplicates > 0 && (
        <p className="warn">
          {summary.duplicates} file(s) already exist in the destination by name. They
          will still be uploaded — deduplication is a separate, later step.
        </p>
      )}

      <p>
        <label>
          <input
            type="checkbox"
            checked={duplicatesOnly}
            onChange={(e) => setDuplicatesOnly(e.target.checked)}
          />{' '}
          Only files already in the destination
        </label>
      </p>

      <p className="muted">
        Showing {rows.length} of {total}.
      </p>

      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Destination</th>
            <th>Date</th>
            <th>Source</th>
            <th>Place</th>
            <th>Already there</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.archive_name}/${row.path}`}>
              <td>{row.name}</td>
              <td>
                {row.target_folder ?? '—'}
                {row.target_name !== row.name && row.target_name
                  ? ` / ${row.target_name}`
                  : ''}
              </td>
              <td>{when(row)}</td>
              <td>{row.capture_source ?? '—'}</td>
              <td>{row.place ?? '—'}</td>
              <td className={row.duplicate_of ? 'warn' : undefined}>
                {row.duplicate_of ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && <p>Nothing planned yet. Run Scan, Pair, then Plan.</p>}
    </>
  )
}
```

- [x] **Step 6: Add the route and the nav link**

In `web/src/App.tsx`, add the import beside the other page imports:

```tsx
import { ReviewPage } from './pages/ReviewPage'
```

and add this route inside `<Routes>`, after the `/actions/:actionId` route:

```tsx
          <Route path="/review" element={<ReviewPage />} />
```

In `web/src/components/Nav.tsx`, add the link inside the Activity section,
before the Jobs link:

```tsx
        <NavLink to="/review">Review Plan</NavLink>
```

- [x] **Step 7: Add the muted style**

Append to `web/src/styles.css`:

```css
.muted { opacity: 0.65; font-size: 0.85rem; }

table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid rgba(128, 128, 128, 0.2); }
th { font-size: 0.75rem; text-transform: uppercase; opacity: 0.6; }
```

- [x] **Step 8: Run the tests and the build**

Run: `cd web && npm test && npm run build`
Expected: 17 tests passed (12 existing plus 5 new), and the build succeeds with
no TypeScript errors.

- [x] **Step 9: Commit**

```bash
git add web/src
git commit -m "feat: review page showing every file and its destination"
```

---

## Task 11: End-to-end verification against the real export

**Files:**
- Create: `tests/test_live_phase2.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all three actions.
- Produces: an opt-in live test proving the pipeline reproduces the spec's measured figures.

- [x] **Step 1: Write the opt-in live test**

Create `tests/test_live_phase2.py`:

```python
"""Opt-in end-to-end run against the real archives.

Run with:
    PHOTOLIB_HOME=/path/to/repo uv run pytest -m live tests/test_live_phase2.py -v

Reads only archive indexes and sidecars — a few megabytes in total. Writes
nothing to Drive.
"""

from __future__ import annotations

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.pair_metadata import Params as PairParams
from photolib.actions.pair_metadata import run as pair
from photolib.actions.plan_organize import Params as PlanParams
from photolib.actions.plan_organize import run as plan
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    cfg = Config.load()
    if not cfg.token_path.exists():
        pytest.skip("token.json not present")
    conn = catalog.connect(tmp_path_factory.mktemp("live") / "live.db")
    settings = SettingsRepo(conn)
    real = SettingsRepo(catalog.connect(cfg.db_path))
    for key in ("photos_root", "zip_source"):
        folder = real.get_folder(key)
        if folder is None:
            pytest.skip(f"{key} is not configured; set it in Settings first")
        settings.set_folder(key, folder)

    drive = DriveClient(TokenProvider(cfg.credentials_path, cfg.token_path))
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    yield context
    drive.close()


def test_scan_indexes_seventeen_archives(ctx):
    list(scan(ctx, ScanParams()))
    counts = ScanRepo(ctx.conn).counts()
    assert counts["archives"] == 17
    assert counts["media"] == 1284
    assert counts["sidecars"] == 1276
    assert counts["drive_files"] > 2000


def test_pairing_leaves_no_sidecar_unmatched(ctx):
    list(pair(ctx, PairParams()))
    summary = MediaRepo(ctx.conn).summary()
    assert summary["media"] == 1284
    assert summary["with_sidecar"] >= 1269   # 1284 media, 15 known to lack a sidecar


def test_plan_assigns_a_destination_to_every_file(ctx):
    list(plan(ctx, PlanParams()))
    summary = MediaRepo(ctx.conn).summary()
    assert summary["planned"] == 1284
    assert summary["unplanned"] == 0
    assert summary["pending"] == 1284      # nothing is ever withheld
    assert summary["duplicates"] >= 480    # measured overlap with the destination


def test_no_target_collides(ctx):
    rows = MediaRepo(ctx.conn).all_media()
    targets = [(r["target_folder"], r["target_name"]) for r in rows]
    assert len(targets) == len(set(targets))
```

- [x] **Step 2: Run the live test**

Run: `PHOTOLIB_HOME=$(git rev-parse --show-toplevel) uv run pytest -m live tests/test_live_phase2.py -v`
Expected: 4 passed. If a count differs from the spec's figures, **stop** — the
export has changed since it was measured, and the discrepancy needs
investigating before Phase 3 uploads anything.

- [x] **Step 3: Document the phase in the README**

Add this section to `README.md`, after the "Running" section:

````markdown
## The pipeline

Run these in order from the UI. The first three are safe to repeat — none of
them writes to Drive.

| Action | Does | Writes to Drive |
| --- | --- | --- |
| Check Connection | Verifies credentials and folders | No |
| Scan Archives | Indexes archive contents and the destination folder | No |
| Pair Metadata | Matches sidecars to media across archive parts | No |
| Plan Organization | Resolves dates, places, duplicates, destinations | No |

Then open **Review Plan** to see every file and where it would go.

Organised photos are destined for `Photos/YYYY-MM/`. The existing `back_*`
folders are indexed for duplicate detection and are never read from, written to,
renamed, or moved.

Files that already exist in the destination are flagged but **still uploaded** —
deduplication is a deliberate later step, not part of this pipeline.
````

- [x] **Step 4: Run the full suite one final time**

```bash
uv run pytest -q
cd web && npm test && npm run build
```

Expected: all backend tests pass, 17 frontend tests pass, build succeeds.

- [x] **Step 5: Commit**

```bash
git add tests/test_live_phase2.py README.md
git commit -m "test: end-to-end live verification of the knowledge pipeline"
```

---

## Self-review notes

Checked against the spec and the overriding decisions:

- **Spec coverage.** Scan Archives (Task 4), Pair Metadata (Task 6), Plan Organization (Task 8) and the Review page (Task 10) are the four Phase 2 deliverables in the spec's build order. The `sidecars`, `media`, `drive_files` and `geocache` tables from the spec's data model are created in Task 1. The two Takeout naming quirks the spec calls out — the `(N)` index outside the extension, and the 51-character truncation — are handled in Task 2 and asserted there directly.
- **Deliberate omissions**, each stated with its reason above: EXIF date extraction (only 15 of 1,284 files would use it), and duplicate-driven upload skipping (overridden by the operator).
- **`upload_status` has no `'skipped'` value** at the schema level, so decision 3 cannot be violated by a later code change without a migration. Task 8 asserts it, and Task 11 asserts it against the real export.
- **Type consistency.** `ScanRepo` and `MediaRepo` method names used in Tasks 4, 6, 8 and 9 match their definitions in Tasks 3 and 5. `ProgressEvent(message, progress, level)` and the `ID`/`TITLE`/`DESCRIPTION`/`ORDER`/`Params`/`run` module contract match `photolib/actions/base.py` and the registry's requirements as built in Phase 1.
- **Action ordering.** `check_connection` is 0; this phase uses 10, 20, 30, leaving room for Phase 3's `organize` and `clear_stale_trees`.

# Phase 1: Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundation of the Google Photos Organizer web app — Drive client, remote ZIP reader, SQLite catalog, FastAPI skeleton, background job runner, and a Settings page with a Drive folder picker — ending with a running app you can point at your Drive folders.

**Architecture:** A FastAPI backend serves a React/Vite SPA on localhost. All state lives in a SQLite catalog. Long-running work runs in a background job runner that streams progress to the browser over SSE; capabilities are registered as "actions" that the frontend renders as pages automatically. Drive access uses `google-auth` for credential refresh and raw `httpx` REST calls for everything else, because byte-range reads and resumable uploads are awkward through the official client library.

**Tech Stack:** Python 3.12 (via `uv`), FastAPI, uvicorn, httpx, pydantic v2, google-auth, stdlib `sqlite3` and `zlib`; React 18 + TypeScript + Vite; pytest + pytest-asyncio; Vitest.

## Global Constraints

- Python 3.12, managed exclusively through `uv`. Never invoke system `python3` (it is 3.9 and will fail).
- All Drive REST calls go through `httpx`. Do not add `google-api-python-client`.
- Only `google-auth` and `google-auth-oauthlib` are used for credentials, never for API calls.
- Credentials live at repo root: `credentials.json` and `token.json`. Both are gitignored and MUST NEVER be committed, printed to logs, or included in test fixtures.
- The OAuth token already carries the `https://www.googleapis.com/auth/drive` scope. Do not trigger a new consent flow if `token.json` refreshes successfully.
- No test may perform a real network call. Live Drive tests are opt-in only, marked `@pytest.mark.live`, and deselected by default.
- SQLite access is stdlib `sqlite3` with `row_factory = sqlite3.Row`. No ORM.
- Every mutating Drive operation in later phases must be resumable; Phase 1 establishes the job/status plumbing that makes that possible.
- Backend package name is `photolib`. Frontend lives in `web/`.
- Frontend talks to the backend at `/api`, proxied by Vite in development.

---

## File Structure

**Backend (`photolib/`)**

| File | Responsibility |
| --- | --- |
| `config.py` | Resolve repo paths, database location, credential paths from env |
| `db/schema.sql` | Full DDL for the Phase 1 tables |
| `db/catalog.py` | Connection factory, migration runner |
| `db/settings_repo.py` | Read/write app settings key-value pairs |
| `db/jobs_repo.py` | Persist jobs and job events |
| `drive/auth.py` | Load and refresh OAuth credentials, produce access tokens |
| `drive/errors.py` | Error taxonomy plus retry/backoff decorator |
| `drive/client.py` | `DriveClient`: metadata, listing, byte-range reads |
| `ziparchive/reader.py` | Remote ZIP central-directory parsing and single-entry extraction |
| `actions/base.py` | `Action` protocol, `ProgressEvent`, parameter schema plumbing |
| `actions/registry.py` | Auto-discovery and lookup of action modules |
| `actions/check_connection.py` | First real action: verify Drive access and configured folders |
| `jobs/broker.py` | In-process pub/sub for live job events |
| `jobs/runner.py` | Queue plus single background worker |
| `api/app.py` | FastAPI application factory and router wiring |
| `api/routes_settings.py` | Settings GET/PUT |
| `api/routes_drive.py` | Folder browsing endpoints backing the picker |
| `api/routes_actions.py` | List actions, launch jobs |
| `api/routes_jobs.py` | Job list, detail, SSE stream |
| `main.py` | uvicorn entry point |

**Frontend (`web/src/`)**

| File | Responsibility |
| --- | --- |
| `api/client.ts` | Typed fetch wrapper and endpoint functions |
| `App.tsx` | Router and application shell |
| `components/Nav.tsx` | Sidebar navigation, including auto-listed actions |
| `components/FolderPicker.tsx` | Modal Drive folder browser |
| `components/JobProgress.tsx` | SSE subscription and live log panel |
| `pages/SettingsPage.tsx` | Configure `photos_root` and `zip_source` |
| `pages/ActionPage.tsx` | Generic action form + run + progress |
| `pages/JobsPage.tsx` | Job history and detail |

**Tests (`tests/`)**

| File | Covers |
| --- | --- |
| `conftest.py` | Temp database, fake Drive fixtures |
| `fixtures/zipbuilder.py` | Builds real ZIP bytes in memory with known CRCs |
| `fakes/fake_drive.py` | In-memory Drive stand-in sharing `DriveClient`'s interface |
| `test_catalog.py`, `test_settings_repo.py`, `test_jobs_repo.py` | Persistence |
| `test_zip_reader.py` | ZIP parsing and extraction |
| `test_drive_auth.py`, `test_drive_client.py`, `test_drive_errors.py` | Drive layer |
| `test_actions.py`, `test_jobs_runner.py` | Action registry and worker |
| `test_api_settings.py`, `test_api_drive.py`, `test_api_actions.py`, `test_api_jobs.py` | HTTP surface |

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `photolib/__init__.py`
- Create: `photolib/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `photolib.config.Config` dataclass with fields `repo_root: Path`, `db_path: Path`, `credentials_path: Path`, `token_path: Path`, `thumbnail_cache_dir: Path`; and `Config.load() -> Config` which honours the `PHOTOLIB_HOME` environment variable.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "photolib"
version = "0.1.0"
description = "Organize a Google Photos Takeout archive stored in Google Drive"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.28",
    "pydantic>=2.9",
    "google-auth>=2.35",
    "google-auth-oauthlib>=1.2",
    "sse-starlette>=2.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "anyio>=4.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["photolib"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "live: hits the real Google Drive API; deselected by default",
]
addopts = "-m 'not live'"
```

- [ ] **Step 2: Create the virtual environment and install**

```bash
cd "$(git rev-parse --show-toplevel)"
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Expected: a `.venv/` directory using Python 3.12, with all dependencies resolved.

- [ ] **Step 3: Append frontend and app artifacts to `.gitignore`**

Add these lines to the existing `.gitignore` (keep the current contents):

```
node_modules/
web/dist/
*.db
.cache/
```

- [ ] **Step 4: Write the failing test**

Create `tests/__init__.py` as an empty file, then `tests/test_config.py`:

```python
import os
from pathlib import Path

from photolib.config import Config


def test_load_defaults_to_repo_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    assert cfg.repo_root == tmp_path
    assert cfg.db_path == tmp_path / "photolib.db"
    assert cfg.credentials_path == tmp_path / "credentials.json"
    assert cfg.token_path == tmp_path / "token.json"
    assert cfg.thumbnail_cache_dir == tmp_path / ".cache" / "thumbnails"


def test_load_without_env_uses_package_parent(monkeypatch):
    monkeypatch.delenv("PHOTOLIB_HOME", raising=False)
    cfg = Config.load()
    assert (cfg.repo_root / "pyproject.toml").exists()
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.config'`

- [ ] **Step 6: Write the implementation**

Create `photolib/__init__.py` as an empty file, then `photolib/config.py`:

```python
"""Path and environment resolution for the application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    repo_root: Path
    db_path: Path
    credentials_path: Path
    token_path: Path
    thumbnail_cache_dir: Path

    @classmethod
    def load(cls) -> "Config":
        env_home = os.environ.get("PHOTOLIB_HOME")
        root = Path(env_home) if env_home else Path(__file__).resolve().parent.parent
        return cls(
            repo_root=root,
            db_path=root / "photolib.db",
            credentials_path=root / "credentials.json",
            token_path=root / "token.json",
            thumbnail_cache_dir=root / ".cache" / "thumbnails",
        )
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore photolib/__init__.py photolib/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: project scaffolding and path configuration"
```

---

## Task 2: SQLite catalog and migrations

**Files:**
- Create: `photolib/db/__init__.py`
- Create: `photolib/db/schema.sql`
- Create: `photolib/db/catalog.py`
- Create: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `photolib.config.Config`.
- Produces: `photolib.db.catalog.connect(db_path: Path) -> sqlite3.Connection` (applies migrations, enables foreign keys and WAL, sets `row_factory`), and `photolib.db.catalog.SCHEMA_VERSION: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog.py`:

```python
import sqlite3

from photolib.db import catalog


def test_connect_creates_all_tables(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"settings", "archives", "entries", "jobs", "job_events"} <= names


def test_connect_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    catalog.connect(db).close()
    conn = catalog.connect(db)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == catalog.SCHEMA_VERSION


def test_rows_are_mappings(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    conn.execute("INSERT INTO settings (key, value) VALUES ('a', 'b')")
    row = conn.execute("SELECT * FROM settings").fetchone()
    assert row["key"] == "a"
    assert row["value"] == "b"


def test_foreign_keys_are_enforced(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    try:
        conn.execute(
            "INSERT INTO job_events (job_id, ts, level, message) "
            "VALUES ('nonexistent', 0, 'info', 'x')"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return
    raise AssertionError("foreign key constraint was not enforced")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.db'`

- [ ] **Step 3: Write the schema**

Create `photolib/db/__init__.py` as an empty file, then `photolib/db/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS archives (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    modified_time TEXT,
    indexed_at    TEXT
);

CREATE TABLE IF NOT EXISTS entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id          INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    path                TEXT NOT NULL,
    name                TEXT NOT NULL,
    crc32               INTEGER NOT NULL,
    size                INTEGER NOT NULL,
    compressed_size     INTEGER NOT NULL,
    method              INTEGER NOT NULL,
    local_header_offset INTEGER NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('media', 'sidecar')),
    UNIQUE (archive_id, path)
);

CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);

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
    finished_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS job_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);
```

- [ ] **Step 4: Write the connection factory**

Create `photolib/db/catalog.py`:

```python
"""SQLite connection and migration handling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the catalog, creating or migrating the schema as needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    conn.executescript(_SCHEMA_PATH.read_text())
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
```

- [ ] **Step 5: Ensure the SQL file ships with the package**

Add to `pyproject.toml` under the existing `[tool.hatch.build.targets.wheel]` section:

```toml
[tool.hatch.build.targets.wheel.force-include]
"photolib/db/schema.sql" = "photolib/db/schema.sql"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add photolib/db pyproject.toml tests/test_catalog.py
git commit -m "feat: sqlite catalog with schema migrations"
```

---

## Task 3: Settings repository

**Files:**
- Create: `photolib/db/settings_repo.py`
- Create: `tests/conftest.py`
- Create: `tests/test_settings_repo.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect`.
- Produces: `photolib.db.settings_repo.SettingsRepo(conn)` with `get(key: str, default: str | None = None) -> str | None`, `set(key: str, value: str) -> None`, `all() -> dict[str, str]`, `get_folder(key: str) -> FolderRef | None`, `set_folder(key: str, folder: FolderRef) -> None`; and the `FolderRef` pydantic model with fields `id: str` and `name: str`. Setting keys are the constants `PHOTOS_ROOT = "photos_root"` and `ZIP_SOURCE = "zip_source"`.

- [ ] **Step 1: Write the shared test fixture**

Create `tests/conftest.py`:

```python
import pytest

from photolib.db import catalog


@pytest.fixture
def conn(tmp_path):
    connection = catalog.connect(tmp_path / "test.db")
    yield connection
    connection.close()
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_settings_repo.py`:

```python
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo


def test_get_missing_returns_default(conn):
    repo = SettingsRepo(conn)
    assert repo.get("nope") is None
    assert repo.get("nope", "fallback") == "fallback"


def test_set_then_get(conn):
    repo = SettingsRepo(conn)
    repo.set("colour", "blue")
    assert repo.get("colour") == "blue"


def test_set_overwrites(conn):
    repo = SettingsRepo(conn)
    repo.set("colour", "blue")
    repo.set("colour", "green")
    assert repo.get("colour") == "green"
    assert repo.all() == {"colour": "green"}


def test_folder_round_trip(conn):
    repo = SettingsRepo(conn)
    repo.set_folder(PHOTOS_ROOT, FolderRef(id="abc123", name="Photos"))
    got = repo.get_folder(PHOTOS_ROOT)
    assert got == FolderRef(id="abc123", name="Photos")


def test_get_folder_missing_returns_none(conn):
    assert SettingsRepo(conn).get_folder(PHOTOS_ROOT) is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_settings_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.db.settings_repo'`

- [ ] **Step 4: Write the implementation**

Create `photolib/db/settings_repo.py`:

```python
"""Key-value application settings stored in the catalog."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

PHOTOS_ROOT = "photos_root"
ZIP_SOURCE = "zip_source"


class FolderRef(BaseModel):
    """A Drive folder chosen by the user."""

    id: str
    name: str


class SettingsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def all(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM settings")
        }

    def get_folder(self, key: str) -> FolderRef | None:
        raw = self.get(key)
        return FolderRef.model_validate_json(raw) if raw else None

    def set_folder(self, key: str, folder: FolderRef) -> None:
        self.set(key, folder.model_dump_json())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings_repo.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add photolib/db/settings_repo.py tests/conftest.py tests/test_settings_repo.py
git commit -m "feat: settings repository with folder references"
```

---

## Task 4: Remote ZIP reader

This is the component that makes the whole project viable: it reads a ZIP's
index and extracts individual files using byte ranges, so a 2.15 GB archive
never has to be downloaded to retrieve one photo.

**Files:**
- Create: `photolib/ziparchive/__init__.py`
- Create: `photolib/ziparchive/reader.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/zipbuilder.py`
- Create: `tests/test_zip_reader.py`

**Interfaces:**
- Consumes: nothing (deliberately network-free — it receives a reader callable).
- Produces:
  - `photolib.ziparchive.reader.ZipEntry` dataclass: `path: str`, `name: str`, `crc32: int`, `size: int`, `compressed_size: int`, `method: int`, `local_header_offset: int`.
  - `photolib.ziparchive.reader.RangeReader` type alias: `Callable[[int, int], bytes]`, called with inclusive start and end offsets.
  - `photolib.ziparchive.reader.read_central_directory(read_range: RangeReader, total_size: int) -> list[ZipEntry]`.
  - `photolib.ziparchive.reader.extract_entry(read_range: RangeReader, entry: ZipEntry) -> bytes` — inflates and verifies CRC32, raising `CorruptEntryError` on mismatch.
  - `photolib.ziparchive.reader.CorruptEntryError(Exception)`.

- [ ] **Step 1: Write the ZIP fixture builder**

Create `tests/fixtures/__init__.py` as an empty file, then `tests/fixtures/zipbuilder.py`:

```python
"""Builds real ZIP archives in memory so the reader can be tested offline."""

from __future__ import annotations

import io
import zipfile


def build_zip(files: dict[str, bytes], compress: bool = True) -> bytes:
    """Produce ZIP bytes containing `files`, mapping archive path to content."""
    buf = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", method) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def build_zip64(files: dict[str, bytes]) -> bytes:
    """Produce a ZIP that carries ZIP64 end-of-central-directory records."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(zipfile.ZipInfo("forced-zip64.txt"), b"x")
        for path, content in files.items():
            zf.writestr(path, content)
    data = bytearray(buf.getvalue())
    return bytes(data)


def reader_for(data: bytes):
    """Return a RangeReader over an in-memory archive, with inclusive bounds."""
    def read_range(start: int, end: int) -> bytes:
        return bytes(data[start : end + 1])

    return read_range
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_zip_reader.py`:

```python
import zlib

import pytest

from photolib.ziparchive.reader import (
    CorruptEntryError,
    extract_entry,
    read_central_directory,
)
from tests.fixtures.zipbuilder import build_zip, reader_for


def test_lists_all_entries():
    data = build_zip({"a/one.txt": b"hello", "a/two.txt": b"world"})
    entries = read_central_directory(reader_for(data), len(data))
    assert {e.path for e in entries} == {"a/one.txt", "a/two.txt"}


def test_entry_carries_name_size_and_crc():
    content = b"hello world" * 100
    data = build_zip({"deep/nested/photo.HEIC": content})
    (entry,) = read_central_directory(reader_for(data), len(data))
    assert entry.name == "photo.HEIC"
    assert entry.size == len(content)
    assert entry.crc32 == zlib.crc32(content)
    assert entry.method == 8


def test_extract_round_trips_deflated_content():
    content = b"the quick brown fox " * 500
    data = build_zip({"x.bin": content})
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    assert extract_entry(read_range, entry) == content


def test_extract_round_trips_stored_content():
    content = b"uncompressed payload"
    data = build_zip({"x.bin": content}, compress=False)
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    assert entry.method == 0
    assert extract_entry(read_range, entry) == content


def test_extract_rejects_corrupt_content():
    content = b"payload that will be damaged"
    data = build_zip({"x.bin": content})
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    tampered = entry.__class__(**{**entry.__dict__, "crc32": entry.crc32 ^ 0xFFFF})
    with pytest.raises(CorruptEntryError):
        extract_entry(read_range, tampered)


def test_handles_takeout_style_names():
    data = build_zip({
        "Takeout/Google Photos/Photos from 2022/IMG_9004.MOV": b"video",
        "Takeout/Google Photos/Photos from 2022/"
        "IMG_9004.MOV.supplemental-metadata.json": b"{}",
        "Takeout/Google Photos/Photos from 2026/IMG_7324(1).PNG": b"png",
    })
    entries = read_central_directory(reader_for(data), len(data))
    names = {e.name for e in entries}
    assert "IMG_9004.MOV" in names
    assert "IMG_7324(1).PNG" in names
    assert "IMG_9004.MOV.supplemental-metadata.json" in names


def test_directory_entries_are_skipped():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("folder/", b"")
        zf.writestr("folder/file.txt", b"data")
    data = buf.getvalue()
    entries = read_central_directory(reader_for(data), len(data))
    assert [e.path for e in entries] == ["folder/file.txt"]


def test_tail_smaller_than_probe_window():
    data = build_zip({"tiny.txt": b"a"})
    assert len(data) < 1 << 20
    entries = read_central_directory(reader_for(data), len(data))
    assert len(entries) == 1
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_zip_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.ziparchive'`

- [ ] **Step 4: Write the implementation**

Create `photolib/ziparchive/__init__.py` as an empty file, then `photolib/ziparchive/reader.py`:

```python
"""Read ZIP archives over byte ranges, without downloading them whole.

A ZIP stores its index (the "central directory") at the end of the file, and
each entry's compressed bytes at a recorded offset. That lets us fetch a small
tail to learn what an archive contains, then fetch only the bytes of the one
entry we want.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from typing import Callable

RangeReader = Callable[[int, int], bytes]

EOCD_SIGNATURE = b"PK\x05\x06"
EOCD64_SIGNATURE = b"PK\x06\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
PROBE_SIZE = 1 << 20
STORED = 0
DEFLATED = 8


class CorruptEntryError(Exception):
    """Raised when extracted bytes do not match the recorded CRC32."""


class MalformedArchiveError(Exception):
    """Raised when the archive has no readable central directory."""


@dataclass
class ZipEntry:
    path: str
    name: str
    crc32: int
    size: int
    compressed_size: int
    method: int
    local_header_offset: int


def read_central_directory(read_range: RangeReader, total_size: int) -> list[ZipEntry]:
    """Return every non-directory entry recorded in the archive's index."""
    probe_len = min(total_size, PROBE_SIZE)
    tail = read_range(total_size - probe_len, total_size - 1)

    eocd = tail.rfind(EOCD_SIGNATURE)
    if eocd < 0:
        raise MalformedArchiveError("no end-of-central-directory record found")
    _, cd_size, cd_offset = struct.unpack("<HII", tail[eocd + 10 : eocd + 20])

    saturated = cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF
    eocd64 = tail.rfind(EOCD64_SIGNATURE)
    if saturated and eocd64 >= 0:
        _, cd_size, cd_offset = struct.unpack("<QQQ", tail[eocd64 + 32 : eocd64 + 56])

    directory = read_range(cd_offset, cd_offset + cd_size - 1)
    return _parse_central_directory(directory)


def _parse_central_directory(directory: bytes) -> list[ZipEntry]:
    entries: list[ZipEntry] = []
    pos = 0
    while pos + 46 <= len(directory) and directory[pos : pos + 4] == CENTRAL_SIGNATURE:
        (method,) = struct.unpack("<H", directory[pos + 10 : pos + 12])
        crc, comp_size, size, name_len, extra_len, comment_len = struct.unpack(
            "<IIIHHH", directory[pos + 16 : pos + 34]
        )
        (header_offset,) = struct.unpack("<I", directory[pos + 42 : pos + 46])
        path = directory[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        pos += 46 + name_len + extra_len + comment_len
        if path.endswith("/"):
            continue
        entries.append(
            ZipEntry(
                path=path,
                name=os.path.basename(path),
                crc32=crc,
                size=size,
                compressed_size=comp_size,
                method=method,
                local_header_offset=header_offset,
            )
        )
    return entries


def extract_entry(read_range: RangeReader, entry: ZipEntry) -> bytes:
    """Fetch and decompress one entry, verifying it against its CRC32."""
    header = read_range(entry.local_header_offset, entry.local_header_offset + 29)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = entry.local_header_offset + 30 + name_len + extra_len

    if entry.compressed_size == 0:
        raw = b""
    else:
        raw = read_range(start, start + entry.compressed_size - 1)

    content = zlib.decompress(raw, -15) if entry.method == DEFLATED else raw

    if zlib.crc32(content) != entry.crc32:
        raise CorruptEntryError(
            f"CRC mismatch for {entry.path}: "
            f"expected {entry.crc32}, got {zlib.crc32(content)}"
        )
    return content
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_zip_reader.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add photolib/ziparchive tests/fixtures tests/test_zip_reader.py
git commit -m "feat: remote zip reader with range-based single-entry extraction"
```

---

## Task 5: Drive authentication

**Files:**
- Create: `photolib/drive/__init__.py`
- Create: `photolib/drive/auth.py`
- Create: `tests/test_drive_auth.py`

**Interfaces:**
- Consumes: `photolib.config.Config`.
- Produces: `photolib.drive.auth.TokenProvider(credentials_path: Path, token_path: Path)` with `access_token() -> str` (refreshing when expired and persisting the refreshed token) and `is_configured() -> bool`; plus `photolib.drive.auth.MissingCredentialsError(Exception)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_drive_auth.py`:

```python
import json

import pytest

from photolib.drive.auth import MissingCredentialsError, TokenProvider

VALID_TOKEN = {
    "token": "cached-access-token",
    "refresh_token": "refresh-me",
    "token_uri": "https://oauth2.example/token",
    "client_id": "cid",
    "client_secret": "secret",
    "scopes": ["https://www.googleapis.com/auth/drive"],
    "expiry": "2099-01-01T00:00:00Z",
}


def write_token(tmp_path, payload):
    path = tmp_path / "token.json"
    path.write_text(json.dumps(payload))
    return path


def test_is_configured_false_when_files_absent(tmp_path):
    provider = TokenProvider(tmp_path / "creds.json", tmp_path / "token.json")
    assert provider.is_configured() is False


def test_is_configured_true_when_token_present(tmp_path):
    token = write_token(tmp_path, VALID_TOKEN)
    (tmp_path / "creds.json").write_text("{}")
    provider = TokenProvider(tmp_path / "creds.json", token)
    assert provider.is_configured() is True


def test_access_token_returns_unexpired_token(tmp_path):
    token = write_token(tmp_path, VALID_TOKEN)
    provider = TokenProvider(tmp_path / "creds.json", token)
    assert provider.access_token() == "cached-access-token"


def test_access_token_refreshes_when_expired(tmp_path, monkeypatch):
    expired = {**VALID_TOKEN, "expiry": "2000-01-01T00:00:00Z"}
    token = write_token(tmp_path, expired)
    provider = TokenProvider(tmp_path / "creds.json", token)

    def fake_refresh(self, request):
        self.token = "fresh-access-token"
        self.expiry = None

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.refresh", fake_refresh
    )
    assert provider.access_token() == "fresh-access-token"
    assert json.loads(token.read_text())["token"] == "fresh-access-token"


def test_access_token_raises_when_missing(tmp_path):
    provider = TokenProvider(tmp_path / "creds.json", tmp_path / "token.json")
    with pytest.raises(MissingCredentialsError):
        provider.access_token()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_drive_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.drive'`

- [ ] **Step 3: Write the implementation**

Create `photolib/drive/__init__.py` as an empty file, then `photolib/drive/auth.py`:

```python
"""OAuth credential loading and refresh for the Drive API."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/drive"]


class MissingCredentialsError(Exception):
    """Raised when token.json is absent or unreadable."""


class TokenProvider:
    """Supplies a valid Drive access token, refreshing it when necessary."""

    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._creds: Credentials | None = None

    def is_configured(self) -> bool:
        return self._token_path.exists()

    def access_token(self) -> str:
        creds = self._load()
        if not creds.valid:
            creds.refresh(Request())
            self._persist(creds)
        return creds.token

    def _load(self) -> Credentials:
        if self._creds is not None:
            return self._creds
        if not self._token_path.exists():
            raise MissingCredentialsError(
                f"{self._token_path} not found; authorise the app first"
            )
        self._creds = Credentials.from_authorized_user_file(
            str(self._token_path), SCOPES
        )
        return self._creds

    def _persist(self, creds: Credentials) -> None:
        self._token_path.write_text(creds.to_json())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_drive_auth.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add photolib/drive/__init__.py photolib/drive/auth.py tests/test_drive_auth.py
git commit -m "feat: drive oauth token provider with refresh"
```

---

## Task 6: Drive HTTP client with retry

**Files:**
- Create: `photolib/drive/errors.py`
- Create: `photolib/drive/client.py`
- Create: `tests/test_drive_client.py`

**Interfaces:**
- Consumes: `photolib.drive.auth.TokenProvider`.
- Produces:
  - `photolib.drive.errors.DriveError(Exception)`, `RateLimitedError(DriveError)`, `TransientError(DriveError)`, `NotFoundError(DriveError)`.
  - `photolib.drive.errors.retry(fn)` decorator: up to 5 attempts, exponential backoff with jitter, retrying only `RateLimitedError` and `TransientError`.
  - `photolib.drive.client.DriveFile` pydantic model: `id: str`, `name: str`, `mime_type: str`, `size: int | None`, `md5: str | None`, `modified_time: str | None`, `parents: list[str]`, and property `is_folder: bool`.
  - `photolib.drive.client.DriveClient(token_provider, http=None)` with `get_file(file_id: str) -> DriveFile`, `list_children(folder_id: str, folders_only: bool = False) -> list[DriveFile]`, `read_range(file_id: str, start: int, end: int) -> bytes`, and `close() -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_drive_client.py`:

```python
import httpx
import pytest

from photolib.drive.client import DriveClient
from photolib.drive.errors import NotFoundError, RateLimitedError, retry


class StubTokens:
    def access_token(self) -> str:
        return "test-token"

    def is_configured(self) -> bool:
        return True


def client_with(handler) -> DriveClient:
    transport = httpx.MockTransport(handler)
    return DriveClient(StubTokens(), http=httpx.Client(transport=transport))


def test_get_file_parses_metadata():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={
            "id": "f1", "name": "photo.HEIC", "mimeType": "image/heic",
            "size": "1234", "md5Checksum": "abc", "parents": ["p1"],
            "modifiedTime": "2026-01-01T00:00:00Z",
        })

    file = client_with(handler).get_file("f1")
    assert file.id == "f1"
    assert file.name == "photo.HEIC"
    assert file.size == 1234
    assert file.md5 == "abc"
    assert file.parents == ["p1"]
    assert file.is_folder is False


def test_folder_detection():
    def handler(request):
        return httpx.Response(200, json={
            "id": "d1", "name": "Photos",
            "mimeType": "application/vnd.google-apps.folder", "parents": [],
        })

    assert client_with(handler).get_file("d1").is_folder is True


def test_list_children_follows_pagination():
    pages = [
        {"files": [{"id": "a", "name": "A", "mimeType": "image/jpeg"}],
         "nextPageToken": "tok2"},
        {"files": [{"id": "b", "name": "B", "mimeType": "image/jpeg"}]},
    ]
    calls = []

    def handler(request):
        calls.append(request.url.params.get("pageToken"))
        return httpx.Response(200, json=pages[len(calls) - 1])

    files = client_with(handler).list_children("parent")
    assert [f.id for f in files] == ["a", "b"]
    assert calls == [None, "tok2"]


def test_list_children_folders_only_filters_query():
    seen = {}

    def handler(request):
        seen["q"] = request.url.params["q"]
        return httpx.Response(200, json={"files": []})

    client_with(handler).list_children("parent", folders_only=True)
    assert "application/vnd.google-apps.folder" in seen["q"]
    assert "'parent' in parents" in seen["q"]
    assert "trashed = false" in seen["q"]


def test_read_range_sends_inclusive_range_header():
    seen = {}

    def handler(request):
        seen["range"] = request.headers["Range"]
        seen["alt"] = request.url.params.get("alt")
        return httpx.Response(206, content=b"0123456789")

    data = client_with(handler).read_range("f1", 100, 109)
    assert data == b"0123456789"
    assert seen["range"] == "bytes=100-109"
    assert seen["alt"] == "media"


def test_missing_file_raises_not_found():
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "File not found"}})

    with pytest.raises(NotFoundError):
        client_with(handler).get_file("nope")


def test_rate_limit_raises_rate_limited():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(RateLimitedError):
        client_with(handler).get_file("f1")


def test_retry_recovers_after_transient_failures(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)
    attempts = {"n": 0}

    @retry
    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimitedError("busy")
        return "ok"

    assert flaky() == "ok"
    assert attempts["n"] == 3


def test_retry_gives_up_and_reraises(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)

    @retry
    def always_busy():
        raise RateLimitedError("busy")

    with pytest.raises(RateLimitedError):
        always_busy()


def test_retry_does_not_retry_not_found(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)
    attempts = {"n": 0}

    @retry
    def missing():
        attempts["n"] += 1
        raise NotFoundError("gone")

    with pytest.raises(NotFoundError):
        missing()
    assert attempts["n"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_drive_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.drive.client'`

- [ ] **Step 3: Write the error taxonomy and retry decorator**

Create `photolib/drive/errors.py`:

```python
"""Drive error classification and retry policy."""

from __future__ import annotations

import functools
import random
import time
from typing import Callable, TypeVar

import httpx

MAX_ATTEMPTS = 5
BASE_DELAY = 0.5
MAX_DELAY = 30.0

T = TypeVar("T")


class DriveError(Exception):
    """Base class for Drive API failures."""


class NotFoundError(DriveError):
    """The requested file or folder does not exist."""


class RateLimitedError(DriveError):
    """Drive asked us to slow down."""


class TransientError(DriveError):
    """A server-side failure that is worth retrying."""


def raise_for_response(response: httpx.Response) -> None:
    """Translate an unsuccessful HTTP response into a typed error."""
    if response.is_success:
        return
    try:
        message = response.json()["error"]["message"]
    except Exception:
        message = response.text[:200]

    status = response.status_code
    if status == 404:
        raise NotFoundError(message)
    if status == 429 or (status == 403 and "rate" in message.lower()):
        raise RateLimitedError(message)
    if status >= 500:
        raise TransientError(f"{status}: {message}")
    raise DriveError(f"{status}: {message}")


def retry(fn: Callable[..., T]) -> Callable[..., T]:
    """Retry rate-limit and transient failures with exponential backoff."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> T:
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return fn(*args, **kwargs)
            except (RateLimitedError, TransientError) as exc:
                last = exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
                time.sleep(delay + random.uniform(0, delay * 0.25))
        assert last is not None
        raise last

    return wrapper
```

- [ ] **Step 4: Write the client**

Create `photolib/drive/client.py`:

```python
"""Thin Drive API v3 client built on httpx.

Uses raw REST rather than google-api-python-client because byte-range reads
and resumable uploads are far easier to express directly.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from photolib.drive.errors import raise_for_response, retry

API_ROOT = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,parents"


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    size: int | None = None
    md5: str | None = Field(default=None, alias="md5Checksum")
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    parents: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME


class DriveClient:
    def __init__(self, token_provider, http: httpx.Client | None = None) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._http.close()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
        if extra:
            headers.update(extra)
        return headers

    @retry
    def get_file(self, file_id: str) -> DriveFile:
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            headers=self._headers(),
        )
        raise_for_response(response)
        return DriveFile.model_validate(response.json())

    def list_children(
        self, folder_id: str, folders_only: bool = False
    ) -> list[DriveFile]:
        query = f"'{folder_id}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"

        files: list[DriveFile] = []
        page_token: str | None = None
        while True:
            payload = self._list_page(query, page_token)
            files.extend(DriveFile.model_validate(f) for f in payload.get("files", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return files

    @retry
    def _list_page(self, query: str, page_token: str | None) -> dict:
        params = {
            "q": query,
            "fields": f"files({FILE_FIELDS}),nextPageToken",
            "pageSize": "1000",
            "orderBy": "folder,name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        response = self._http.get(
            f"{API_ROOT}/files", params=params, headers=self._headers()
        )
        raise_for_response(response)
        return response.json()

    @retry
    def read_range(self, file_id: str, start: int, end: int) -> bytes:
        """Read bytes `start` through `end` inclusive from a file's content."""
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=self._headers({"Range": f"bytes={start}-{end}"}),
        )
        raise_for_response(response)
        return response.content
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_drive_client.py -v`
Expected: 10 passed

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add photolib/drive/errors.py photolib/drive/client.py tests/test_drive_client.py
git commit -m "feat: drive rest client with typed errors and backoff"
```

---

## Task 7: Archive access glue and the fake Drive

Connects the Drive client to the ZIP reader, and provides the in-memory Drive
stand-in that every later test relies on.

**Files:**
- Create: `photolib/archives.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/fake_drive.py`
- Create: `tests/test_archives.py`

**Interfaces:**
- Consumes: `DriveClient`, `ZipEntry`, `read_central_directory`, `extract_entry`.
- Produces:
  - `photolib.archives.drive_range_reader(client: DriveClient, file_id: str) -> RangeReader`.
  - `photolib.archives.list_archive_entries(client: DriveClient, file_id: str, size: int) -> list[ZipEntry]`.
  - `photolib.archives.extract_from_archive(client: DriveClient, file_id: str, entry: ZipEntry) -> bytes`.
  - `photolib.archives.classify(path: str) -> str` returning `"sidecar"` for `.json` paths and `"media"` otherwise.
  - `tests.fakes.fake_drive.FakeDrive` with `add_folder(id, name, parent=None)`, `add_file(id, name, content: bytes, parent, mime_type="application/octet-stream")`, and the same `get_file` / `list_children` / `read_range` surface as `DriveClient`.

- [ ] **Step 1: Write the fake Drive**

Create `tests/fakes/__init__.py` as an empty file, then `tests/fakes/fake_drive.py`:

```python
"""In-memory stand-in for DriveClient, sharing its interface exactly."""

from __future__ import annotations

import hashlib

from photolib.drive.client import FOLDER_MIME, DriveFile
from photolib.drive.errors import NotFoundError


class FakeDrive:
    def __init__(self) -> None:
        self._files: dict[str, DriveFile] = {}
        self._content: dict[str, bytes] = {}

    def add_folder(self, id: str, name: str, parent: str | None = None) -> DriveFile:
        folder = DriveFile(
            id=id, name=name, mimeType=FOLDER_MIME,
            parents=[parent] if parent else [],
        )
        self._files[id] = folder
        return folder

    def add_file(
        self,
        id: str,
        name: str,
        content: bytes,
        parent: str,
        mime_type: str = "application/octet-stream",
    ) -> DriveFile:
        file = DriveFile(
            id=id, name=name, mimeType=mime_type, size=len(content),
            md5Checksum=hashlib.md5(content).hexdigest(), parents=[parent],
        )
        self._files[id] = file
        self._content[id] = content
        return file

    # --- DriveClient interface ---

    def get_file(self, file_id: str) -> DriveFile:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        return self._files[file_id]

    def list_children(
        self, folder_id: str, folders_only: bool = False
    ) -> list[DriveFile]:
        children = [f for f in self._files.values() if folder_id in f.parents]
        if folders_only:
            children = [f for f in children if f.is_folder]
        return sorted(children, key=lambda f: (not f.is_folder, f.name))

    def read_range(self, file_id: str, start: int, end: int) -> bytes:
        if file_id not in self._content:
            raise NotFoundError(f"no content for: {file_id}")
        return self._content[file_id][start : end + 1]

    def close(self) -> None:
        pass
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_archives.py`:

```python
from photolib import archives
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

CONTENTS = {
    "Takeout/Google Photos/Photos from 2022/IMG_9004.MOV": b"movie-bytes" * 50,
    "Takeout/Google Photos/Photos from 2022/"
    "IMG_9004.MOV.supplemental-metadata.json": b'{"title": "IMG_9004.MOV"}',
}


def drive_with_archive() -> tuple[FakeDrive, int]:
    data = build_zip(CONTENTS)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", data, parent="zips")
    return drive, len(data)


def test_lists_entries_from_a_drive_hosted_archive():
    drive, size = drive_with_archive()
    entries = archives.list_archive_entries(drive, "z1", size)
    assert {e.name for e in entries} == {
        "IMG_9004.MOV", "IMG_9004.MOV.supplemental-metadata.json",
    }


def test_extracts_a_single_entry_without_reading_whole_archive():
    drive, size = drive_with_archive()
    entries = archives.list_archive_entries(drive, "z1", size)
    media = next(e for e in entries if e.name == "IMG_9004.MOV")
    assert archives.extract_from_archive(drive, "z1", media) == CONTENTS[media.path]


def test_classify_distinguishes_sidecars_from_media():
    assert archives.classify("a/b/IMG_1.HEIC") == "media"
    assert archives.classify("a/b/IMG_1.MOV") == "media"
    assert archives.classify(
        "a/b/IMG_1.MOV.supplemental-metadata.json"
    ) == "sidecar"
    assert archives.classify("a/b/Anything.JSON") == "sidecar"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_archives.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.archives'`

- [ ] **Step 4: Write the implementation**

Create `photolib/archives.py`:

```python
"""Bridge between Drive-hosted files and the ZIP reader."""

from __future__ import annotations

from photolib.ziparchive.reader import (
    RangeReader,
    ZipEntry,
    extract_entry,
    read_central_directory,
)

SIDECAR = "sidecar"
MEDIA = "media"


def drive_range_reader(client, file_id: str) -> RangeReader:
    """Adapt a Drive file into the RangeReader the ZIP reader expects."""

    def read_range(start: int, end: int) -> bytes:
        return client.read_range(file_id, start, end)

    return read_range


def list_archive_entries(client, file_id: str, size: int) -> list[ZipEntry]:
    return read_central_directory(drive_range_reader(client, file_id), size)


def extract_from_archive(client, file_id: str, entry: ZipEntry) -> bytes:
    return extract_entry(drive_range_reader(client, file_id), entry)


def classify(path: str) -> str:
    return SIDECAR if path.lower().endswith(".json") else MEDIA
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_archives.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add photolib/archives.py tests/fakes tests/test_archives.py
git commit -m "feat: archive access glue and in-memory drive fake"
```

---

## Task 8: Jobs repository

**Files:**
- Create: `photolib/db/jobs_repo.py`
- Create: `tests/test_jobs_repo.py`

**Interfaces:**
- Consumes: `photolib.db.catalog.connect`.
- Produces: `photolib.db.jobs_repo.JobsRepo(conn)` with `create(action: str, params: dict) -> Job`, `get(job_id: str) -> Job | None`, `list(limit: int = 50) -> list[Job]`, `mark_running(job_id)`, `mark_done(job_id)`, `mark_failed(job_id, error: str)`, `update_progress(job_id, progress: float, message: str | None)`, `add_event(job_id, level: str, message: str)`, `events(job_id, after_id: int = 0) -> list[JobEvent]`; plus pydantic models `Job` (fields `id`, `action`, `params: dict`, `status`, `progress`, `message`, `error`, `created_at`, `started_at`, `finished_at`) and `JobEvent` (fields `id`, `job_id`, `ts`, `level`, `message`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_jobs_repo.py`:

```python
from photolib.db.jobs_repo import JobsRepo


def test_create_returns_queued_job(conn):
    job = JobsRepo(conn).create("check_connection", {"deep": True})
    assert job.status == "queued"
    assert job.action == "check_connection"
    assert job.params == {"deep": True}
    assert job.progress == 0.0
    assert job.id


def test_get_round_trips(conn):
    repo = JobsRepo(conn)
    created = repo.create("check_connection", {})
    assert repo.get(created.id) == created


def test_get_unknown_returns_none(conn):
    assert JobsRepo(conn).get("missing") is None


def test_lifecycle_transitions(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_running(job.id)
    assert repo.get(job.id).status == "running"
    assert repo.get(job.id).started_at is not None
    repo.mark_done(job.id)
    assert repo.get(job.id).status == "done"
    assert repo.get(job.id).progress == 1.0
    assert repo.get(job.id).finished_at is not None


def test_failure_records_error(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_failed(job.id, "boom")
    stored = repo.get(job.id)
    assert stored.status == "failed"
    assert stored.error == "boom"
    assert stored.finished_at is not None


def test_progress_updates(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "halfway")
    stored = repo.get(job.id)
    assert stored.progress == 0.5
    assert stored.message == "halfway"


def test_events_are_ordered_and_filterable(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.add_event(job.id, "info", "first")
    repo.add_event(job.id, "warn", "second")
    events = repo.events(job.id)
    assert [e.message for e in events] == ["first", "second"]
    assert [e.level for e in events] == ["info", "warn"]
    later = repo.events(job.id, after_id=events[0].id)
    assert [e.message for e in later] == ["second"]


def test_list_is_newest_first(conn):
    repo = JobsRepo(conn)
    first = repo.create("check_connection", {})
    second = repo.create("check_connection", {})
    ids = [j.id for j in repo.list()]
    assert ids.index(second.id) < ids.index(first.id)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_jobs_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.db.jobs_repo'`

- [ ] **Step 3: Write the implementation**

Create `photolib/db/jobs_repo.py`:

```python
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

    def create(self, action: str, params: dict) -> Job:
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
        self._conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        self._conn.commit()

    def mark_done(self, job_id: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = 'done', progress = 1.0, finished_at = ? "
            "WHERE id = ?",
            (_now(), job_id),
        )
        self._conn.commit()

    def mark_failed(self, job_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE id = ?",
            (error, _now(), job_id),
        )
        self._conn.commit()

    def update_progress(
        self, job_id: str, progress: float, message: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE jobs SET progress = ?, message = COALESCE(?, message) "
            "WHERE id = ?",
            (progress, message, job_id),
        )
        self._conn.commit()

    def add_event(self, job_id: str, level: str, message: str) -> None:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jobs_repo.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add photolib/db/jobs_repo.py tests/test_jobs_repo.py
git commit -m "feat: jobs repository with lifecycle and event log"
```

---

## Task 9: Action protocol, registry, and the first action

This establishes the extensibility contract: every future capability is a module
dropped into `photolib/actions/`, and the frontend renders a page for it
automatically.

**Files:**
- Create: `photolib/actions/__init__.py`
- Create: `photolib/actions/base.py`
- Create: `photolib/actions/registry.py`
- Create: `photolib/actions/check_connection.py`
- Create: `tests/test_actions.py`

**Interfaces:**
- Consumes: `SettingsRepo`, `DriveClient`, `Config`.
- Produces:
  - `photolib.actions.base.ProgressEvent` dataclass: `message: str`, `progress: float | None = None`, `level: str = "info"`.
  - `photolib.actions.base.ActionContext` dataclass: `conn`, `drive`, `settings: SettingsRepo`, `config: Config`.
  - `photolib.actions.base.ActionParams(BaseModel)` — the base every action's `Params` extends; configured with `extra="forbid"` so unknown parameters are rejected rather than silently ignored.
  - `photolib.actions.base.ActionSpec` dataclass: `id: str`, `title: str`, `description: str`, `order: int`, `params_model: type[ActionParams]`, `run: Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]`, and method `json_schema() -> dict`.
  - `photolib.actions.registry.all_actions() -> list[ActionSpec]` (sorted by `order` then `id`) and `get_action(action_id: str) -> ActionSpec`, raising `UnknownActionError`.
  - Every action module declares: `ID`, `TITLE`, `DESCRIPTION`, `ORDER: int`, `class Params(ActionParams)`, and `def run(ctx, params) -> Iterator[ProgressEvent]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_actions.py`:

```python
import pytest

from photolib.actions import check_connection
from photolib.actions.base import ActionContext, ProgressEvent
from photolib.actions.registry import UnknownActionError, all_actions, get_action
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


def make_ctx(conn, drive) -> ActionContext:
    return ActionContext(
        conn=conn, drive=drive, settings=SettingsRepo(conn), config=Config.load()
    )


def test_registry_discovers_check_connection():
    ids = [spec.id for spec in all_actions()]
    assert "check_connection" in ids


def test_registry_specs_are_complete():
    spec = get_action("check_connection")
    assert spec.title
    assert spec.description
    assert spec.json_schema()["type"] == "object"


def test_registry_rejects_unknown_action():
    with pytest.raises(UnknownActionError):
        get_action("no_such_action")


def test_check_connection_reports_missing_settings(conn):
    drive = FakeDrive()
    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "not configured" in text.lower()
    assert any(e.level == "warn" for e in events)


def test_check_connection_reports_configured_folders(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "Global Photos")
    drive.add_folder("zips", "zip-3-22-26")
    drive.add_file("z1", "takeout-001.zip", build_zip({"a.txt": b"x"}), parent="zips")
    drive.add_file("z2", "takeout-002.zip", build_zip({"b.txt": b"y"}), parent="zips")

    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Global Photos"))
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-3-22-26"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "Global Photos" in text
    assert "2 archive" in text
    assert all(e.level != "error" for e in events)


def test_check_connection_reports_unreachable_folder(conn):
    drive = FakeDrive()
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="ghost", name="Gone"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    assert any(e.level == "error" for e in events)


def test_progress_is_monotonic_and_bounded(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "P")
    drive.add_folder("zips", "Z")
    SettingsRepo(conn).set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="P"))
    SettingsRepo(conn).set_folder(ZIP_SOURCE, FolderRef(id="zips", name="Z"))

    values = [
        e.progress
        for e in check_connection.run(make_ctx(conn, drive), check_connection.Params())
        if e.progress is not None
    ]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.actions'`

- [ ] **Step 3: Write the action protocol**

Create `photolib/actions/__init__.py` as an empty file, then `photolib/actions/base.py`:

```python
"""The contract every action implements."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterator

from pydantic import BaseModel, ConfigDict

from photolib.config import Config
from photolib.db.settings_repo import SettingsRepo


class ActionParams(BaseModel):
    """Base for action parameters; unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class ProgressEvent:
    """One unit of feedback from a running action."""

    message: str
    progress: float | None = None
    level: str = "info"


@dataclass
class ActionContext:
    """Everything an action is allowed to reach."""

    conn: sqlite3.Connection
    drive: object
    settings: SettingsRepo
    config: Config


@dataclass
class ActionSpec:
    id: str
    title: str
    description: str
    order: int
    params_model: type[ActionParams]
    run: Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]

    def json_schema(self) -> dict:
        return self.params_model.model_json_schema()
```

- [ ] **Step 4: Write the registry**

Create `photolib/actions/registry.py`:

```python
"""Auto-discovery of action modules.

Any module in this package declaring ID, TITLE, DESCRIPTION, ORDER, Params and
run() becomes an action, and therefore a page in the UI.
"""

from __future__ import annotations

import importlib
import pkgutil

import photolib.actions
from photolib.actions.base import ActionSpec

_REQUIRED = ("ID", "TITLE", "DESCRIPTION", "ORDER", "Params", "run")


class UnknownActionError(KeyError):
    """Raised when an action id does not exist."""


def _discover() -> dict[str, ActionSpec]:
    specs: dict[str, ActionSpec] = {}
    for info in pkgutil.iter_modules(photolib.actions.__path__):
        if info.name in {"base", "registry"}:
            continue
        module = importlib.import_module(f"photolib.actions.{info.name}")
        if not all(hasattr(module, attr) for attr in _REQUIRED):
            continue
        specs[module.ID] = ActionSpec(
            id=module.ID,
            title=module.TITLE,
            description=module.DESCRIPTION,
            order=module.ORDER,
            params_model=module.Params,
            run=module.run,
        )
    return specs


def all_actions() -> list[ActionSpec]:
    return sorted(_discover().values(), key=lambda s: (s.order, s.id))


def get_action(action_id: str) -> ActionSpec:
    specs = _discover()
    if action_id not in specs:
        raise UnknownActionError(action_id)
    return specs[action_id]
```

- [ ] **Step 5: Write the check_connection action**

Create `photolib/actions/check_connection.py`:

```python
"""Verify Drive access and report on the configured folders."""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE
from photolib.drive.errors import DriveError

ID = "check_connection"
TITLE = "Check Connection"
DESCRIPTION = (
    "Verify that Drive credentials work and that the configured "
    "Global Photos and ZIP source folders are reachable."
)
ORDER = 0


class Params(ActionParams):
    pass


_CHECKS = ((PHOTOS_ROOT, "Global Photos folder"), (ZIP_SOURCE, "ZIP source folder"))


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    total = len(_CHECKS)
    for index, (key, label) in enumerate(_CHECKS, start=1):
        progress = index / total
        folder = ctx.settings.get_folder(key)
        if folder is None:
            yield ProgressEvent(
                f"{label} is not configured.", progress=progress, level="warn"
            )
            continue

        try:
            found = ctx.drive.get_file(folder.id)
        except DriveError as exc:
            yield ProgressEvent(
                f"{label} '{folder.name}' is unreachable: {exc}",
                progress=progress,
                level="error",
            )
            continue

        children = ctx.drive.list_children(folder.id)
        archives = [c for c in children if c.name.lower().endswith(".zip")]
        detail = f"{len(children)} item(s)"
        if key == ZIP_SOURCE:
            detail += f", {len(archives)} archive(s)"
        yield ProgressEvent(
            f"{label} '{found.name}' is reachable: {detail}.", progress=progress
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_actions.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add photolib/actions tests/test_actions.py
git commit -m "feat: action protocol, auto-discovery registry, connection check"
```

---

## Task 10: Background job runner

**Files:**
- Create: `photolib/jobs/__init__.py`
- Create: `photolib/jobs/broker.py`
- Create: `photolib/jobs/runner.py`
- Create: `tests/test_jobs_runner.py`

**Interfaces:**
- Consumes: `JobsRepo`, `all_actions`/`get_action`, `ActionContext`.
- Produces:
  - `photolib.jobs.broker.EventBroker` with `publish(job_id: str, payload: dict) -> None`, `subscribe(job_id: str) -> queue.Queue`, `unsubscribe(job_id: str, q: queue.Queue) -> None`.
  - `photolib.jobs.runner.JobRunner(context_factory, repo, broker)` with `submit(action_id: str, params: dict) -> Job`, `start() -> None`, `stop() -> None`, and `wait_idle(timeout: float = 5.0) -> None` for tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_jobs_runner.py`:

```python
import pytest

from photolib.actions.base import ActionContext, ProgressEvent
from photolib.config import Config
from photolib.db.jobs_repo import JobsRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.jobs.broker import EventBroker
from photolib.jobs.runner import JobRunner
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def runner(conn):
    repo = JobsRepo(conn)
    broker = EventBroker()

    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=FakeDrive(), settings=SettingsRepo(conn),
            config=Config.load(),
        )

    r = JobRunner(context_factory=context_factory, repo=repo, broker=broker)
    r.start()
    yield r
    r.stop()


def test_runs_a_job_to_completion(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert JobsRepo(conn).get(job.id).status == "done"


def test_records_events_from_the_action(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    events = JobsRepo(conn).events(job.id)
    assert events
    assert all(e.message for e in events)


def test_failure_is_captured(runner, conn, monkeypatch):
    def explode(ctx, params):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    from photolib.actions import registry

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(
        registry, "get_action", lambda _id: type(spec)(
            id=spec.id, title=spec.title, description=spec.description,
            order=spec.order, params_model=spec.params_model, run=explode,
        )
    )
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    stored = JobsRepo(conn).get(job.id)
    assert stored.status == "failed"
    assert "kaboom" in stored.error


def test_subscribers_receive_live_events(runner, conn):
    broker = runner.broker
    job = runner.submit("check_connection", {})
    queue_ = broker.subscribe(job.id)
    runner.wait_idle()
    broker.publish(job.id, {"type": "sentinel"})
    received = []
    while not queue_.empty():
        received.append(queue_.get_nowait())
    assert any(item.get("type") == "sentinel" for item in received)


def test_jobs_run_one_at_a_time(runner, conn):
    first = runner.submit("check_connection", {})
    second = runner.submit("check_connection", {})
    runner.wait_idle()
    repo = JobsRepo(conn)
    assert repo.get(first.id).status == "done"
    assert repo.get(second.id).status == "done"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_jobs_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.jobs'`

- [ ] **Step 3: Write the event broker**

Create `photolib/jobs/__init__.py` as an empty file, then `photolib/jobs/broker.py`:

```python
"""In-process fan-out of job events to SSE subscribers."""

from __future__ import annotations

import queue
import threading


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self._lock:
            if job_id in self._subscribers and q in self._subscribers[job_id]:
                self._subscribers[job_id].remove(q)
            if job_id in self._subscribers and not self._subscribers[job_id]:
                del self._subscribers[job_id]

    def publish(self, job_id: str, payload: dict) -> None:
        with self._lock:
            targets = list(self._subscribers.get(job_id, []))
        for q in targets:
            q.put(payload)
```

- [ ] **Step 4: Write the runner**

Create `photolib/jobs/runner.py`:

```python
"""A single background worker that executes queued actions."""

from __future__ import annotations

import queue
import threading
import traceback

from photolib.actions import registry
from photolib.db.jobs_repo import Job, JobsRepo
from photolib.jobs.broker import EventBroker


class JobRunner:
    def __init__(self, context_factory, repo: JobsRepo, broker: EventBroker) -> None:
        self._context_factory = context_factory
        self._repo = repo
        self.broker = broker
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        self._thread = None

    def submit(self, action_id: str, params: dict) -> Job:
        registry.get_action(action_id)  # fail fast on unknown ids
        job = self._repo.create(action_id, params)
        self._idle.clear()
        self._queue.put(job.id)
        return job

    def wait_idle(self, timeout: float = 5.0) -> None:
        if not self._idle.wait(timeout):
            raise TimeoutError("job runner did not become idle")

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._execute(job_id)
            finally:
                if self._queue.empty():
                    self._idle.set()

    def _execute(self, job_id: str) -> None:
        job = self._repo.get(job_id)
        if job is None:
            return
        self._repo.mark_running(job_id)
        self._emit(job_id, {"type": "status", "status": "running"})
        try:
            spec = registry.get_action(job.action)
            params = spec.params_model.model_validate(job.params)
            for event in spec.run(self._context_factory(), params):
                self._repo.add_event(job_id, event.level, event.message)
                if event.progress is not None:
                    self._repo.update_progress(job_id, event.progress, event.message)
                self._emit(job_id, {
                    "type": "event", "level": event.level,
                    "message": event.message, "progress": event.progress,
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

    def _emit(self, job_id: str, payload: dict) -> None:
        self.broker.publish(job_id, payload)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jobs_runner.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add photolib/jobs tests/test_jobs_runner.py
git commit -m "feat: background job runner with live event broker"
```

---

## Task 11: FastAPI application, settings and Drive routes

**Files:**
- Create: `photolib/api/__init__.py`
- Create: `photolib/api/app.py`
- Create: `photolib/api/routes_settings.py`
- Create: `photolib/api/routes_drive.py`
- Create: `tests/test_api_settings.py`
- Create: `tests/test_api_drive.py`

**Interfaces:**
- Consumes: everything built so far.
- Produces:
  - `photolib.api.app.create_app(config: Config | None = None, drive=None) -> FastAPI`. When `drive` is supplied the app uses it instead of a real `DriveClient`, which is how tests avoid the network. The app exposes `app.state.conn`, `app.state.drive`, `app.state.settings`, `app.state.jobs`, `app.state.runner`, `app.state.broker`.
  - `GET /api/settings` → `{"photos_root": FolderRef | null, "zip_source": FolderRef | null, "credentials_configured": bool}`.
  - `PUT /api/settings/{key}` with body `{"id": str, "name": str}` → the stored `FolderRef`. Rejects any key outside `photos_root` and `zip_source` with 400.
  - `GET /api/drive/folders?parent=<id>` → `{"parent": {"id","name"}, "folders": [DriveFile...]}`. `parent` defaults to `"root"`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_api_settings.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    drive = FakeDrive()
    drive.add_folder("photos", "Global Photos")
    drive.add_folder("zips", "zip-3-22-26")
    app = create_app(config=Config.load(), drive=drive)
    with TestClient(app) as c:
        yield c


def test_settings_start_empty(client):
    body = client.get("/api/settings").json()
    assert body["photos_root"] is None
    assert body["zip_source"] is None
    assert body["credentials_configured"] is False


def test_put_and_get_photos_root(client):
    response = client.put(
        "/api/settings/photos_root", json={"id": "photos", "name": "Global Photos"}
    )
    assert response.status_code == 200
    assert response.json() == {"id": "photos", "name": "Global Photos"}
    assert client.get("/api/settings").json()["photos_root"]["id"] == "photos"


def test_put_zip_source(client):
    client.put("/api/settings/zip_source", json={"id": "zips", "name": "zip-3-22-26"})
    assert client.get("/api/settings").json()["zip_source"]["name"] == "zip-3-22-26"


def test_unknown_setting_key_is_rejected(client):
    response = client.put("/api/settings/hack", json={"id": "x", "name": "y"})
    assert response.status_code == 400


def test_settings_persist_across_app_instances(client, tmp_path):
    client.put("/api/settings/photos_root", json={"id": "photos", "name": "P"})
    from photolib.api.app import create_app as make

    second = make(config=Config.load(), drive=FakeDrive())
    with TestClient(second) as c2:
        assert c2.get("/api/settings").json()["photos_root"]["id"] == "photos"
```

Create `tests/test_api_drive.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    drive = FakeDrive()
    drive.add_folder("root", "My Drive")
    drive.add_folder("photos", "Global Photos", parent="root")
    drive.add_folder("zips", "zip-3-22-26", parent="root")
    drive.add_folder("nested", "Takeout", parent="zips")
    drive.add_file("z1", "takeout-001.zip", build_zip({"a.txt": b"x"}), parent="zips")
    app = create_app(config=Config.load(), drive=drive)
    with TestClient(app) as c:
        yield c


def test_lists_root_folders_by_default(client):
    body = client.get("/api/drive/folders").json()
    names = [f["name"] for f in body["folders"]]
    assert "Global Photos" in names
    assert "zip-3-22-26" in names


def test_lists_child_folders(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert [f["name"] for f in body["folders"]] == ["Takeout"]


def test_files_are_excluded_from_folder_listing(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert all(f["name"] != "takeout-001.zip" for f in body["folders"])


def test_includes_parent_details(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert body["parent"]["name"] == "zip-3-22-26"


def test_unknown_parent_returns_404(client):
    assert client.get("/api/drive/folders", params={"parent": "ghost"}).status_code == 404
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_settings.py tests/test_api_drive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.api'`

- [x] **Step 3: Write the application factory**

Create `photolib/api/__init__.py` as an empty file, then `photolib/api/app.py`:

```python
"""FastAPI application factory and wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from photolib.config import Config
from photolib.db import catalog
from photolib.db.jobs_repo import JobsRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient
from photolib.jobs.broker import EventBroker
from photolib.jobs.runner import JobRunner
from photolib.actions.base import ActionContext


def create_app(config: Config | None = None, drive=None) -> FastAPI:
    cfg = config or Config.load()
    conn = catalog.connect(cfg.db_path)
    tokens = TokenProvider(cfg.credentials_path, cfg.token_path)
    drive_client = drive if drive is not None else DriveClient(tokens)

    settings = SettingsRepo(conn)
    jobs = JobsRepo(conn)
    broker = EventBroker()

    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=drive_client, settings=settings, config=cfg
        )

    runner = JobRunner(context_factory=context_factory, repo=jobs, broker=broker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner.start()
        yield
        runner.stop()
        conn.close()

    app = FastAPI(title="Photo Library Organizer", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = cfg
    app.state.conn = conn
    app.state.tokens = tokens
    app.state.drive = drive_client
    app.state.settings = settings
    app.state.jobs = jobs
    app.state.broker = broker
    app.state.runner = runner

    from photolib.api import routes_actions, routes_drive, routes_jobs, routes_settings

    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(routes_drive.router, prefix="/api")
    app.include_router(routes_actions.router, prefix="/api")
    app.include_router(routes_jobs.router, prefix="/api")
    return app
```

- [x] **Step 4: Write the settings routes**

Create `photolib/api/routes_settings.py`:

```python
"""Read and update application settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef

router = APIRouter(tags=["settings"])
ALLOWED_KEYS = {PHOTOS_ROOT, ZIP_SOURCE}


@router.get("/settings")
def read_settings(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        PHOTOS_ROOT: settings.get_folder(PHOTOS_ROOT),
        ZIP_SOURCE: settings.get_folder(ZIP_SOURCE),
        "credentials_configured": request.app.state.tokens.is_configured(),
    }


@router.put("/settings/{key}")
def write_setting(key: str, folder: FolderRef, request: Request) -> FolderRef:
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown setting: {key}")
    request.app.state.settings.set_folder(key, folder)
    return folder
```

- [x] **Step 5: Write the Drive browsing routes**

Create `photolib/api/routes_drive.py`:

```python
"""Drive browsing endpoints backing the folder picker."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from photolib.drive.errors import DriveError, NotFoundError

router = APIRouter(tags=["drive"])


@router.get("/drive/folders")
def list_folders(request: Request, parent: str = "root") -> dict:
    drive = request.app.state.drive
    try:
        folders = drive.list_children(parent, folders_only=True)
        try:
            parent_file = drive.get_file(parent)
            parent_info = {"id": parent_file.id, "name": parent_file.name}
        except NotFoundError:
            if parent != "root":
                raise
            parent_info = {"id": "root", "name": "My Drive"}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DriveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "parent": parent_info,
        "folders": [f.model_dump(by_alias=True) for f in folders],
    }
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_settings.py tests/test_api_drive.py -v`
Expected: 10 passed

- [x] **Step 7: Commit**

```bash
git add photolib/api tests/test_api_settings.py tests/test_api_drive.py
git commit -m "feat: fastapi app with settings and drive browsing routes"
```

---

## Task 12: Action and job routes with SSE streaming

**Files:**
- Create: `photolib/api/routes_actions.py`
- Create: `photolib/api/routes_jobs.py`
- Create: `photolib/main.py`
- Create: `tests/test_api_actions.py`
- Create: `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `all_actions`, `get_action`, `JobsRepo`, `JobRunner`, `EventBroker`.
- Produces:
  - `GET /api/actions` → `[{"id","title","description","order","schema"}]`.
  - `POST /api/actions/{id}/run` with a JSON params body → the created `Job`. Unknown id → 404; invalid params → 422.
  - `GET /api/jobs?limit=50` → `[Job]`.
  - `GET /api/jobs/{id}` → `Job`; unknown → 404.
  - `GET /api/jobs/{id}/events?after=0` → `[JobEvent]`.
  - `GET /api/jobs/{id}/stream` → `text/event-stream` of the broker's payloads.
  - `photolib.main:app` — the module-level ASGI app for uvicorn.

- [x] **Step 1: Write the failing tests**

Create `tests/test_api_actions.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
        yield c


def test_lists_actions_with_schema(client):
    actions = client.get("/api/actions").json()
    assert any(a["id"] == "check_connection" for a in actions)
    spec = next(a for a in actions if a["id"] == "check_connection")
    assert spec["title"] == "Check Connection"
    assert spec["description"]
    assert spec["schema"]["type"] == "object"


def test_run_creates_a_job(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    assert job["action"] == "check_connection"
    assert job["status"] in {"queued", "running", "done"}
    assert job["id"]


def test_run_unknown_action_returns_404(client):
    assert client.post("/api/actions/nope/run", json={}).status_code == 404


def test_run_with_unknown_params_returns_422(client):
    response = client.post("/api/actions/check_connection/run", json={"bad": 1})
    assert response.status_code == 422
```

Create `tests/test_api_jobs.py`:

```python
import time

import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
        yield c


def finished_job(client) -> dict:
    job = client.post("/api/actions/check_connection/run", json={}).json()
    for _ in range(100):
        current = client.get(f"/api/jobs/{job['id']}").json()
        if current["status"] in {"done", "failed"}:
            return current
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_job_reaches_a_terminal_state(client):
    assert finished_job(client)["status"] == "done"


def test_job_list_includes_the_run(client):
    job = finished_job(client)
    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert job["id"] in ids


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/missing").status_code == 404


def test_events_are_returned_and_filterable(client):
    job = finished_job(client)
    events = client.get(f"/api/jobs/{job['id']}/events").json()
    assert events
    after = client.get(
        f"/api/jobs/{job['id']}/events", params={"after": events[0]["id"]}
    ).json()
    assert len(after) == len(events) - 1


def test_stream_endpoint_serves_event_stream(client):
    job = finished_job(client)
    with client.stream("GET", f"/api/jobs/{job['id']}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_actions.py tests/test_api_jobs.py -v`
Expected: FAIL — `routes_actions` does not exist, so app creation errors on import

- [x] **Step 3: Write the action routes**

Create `photolib/api/routes_actions.py`:

```python
"""List available actions and launch them as jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from photolib.actions.registry import UnknownActionError, all_actions, get_action

router = APIRouter(tags=["actions"])


@router.get("/actions")
def list_actions() -> list[dict]:
    return [
        {
            "id": spec.id,
            "title": spec.title,
            "description": spec.description,
            "order": spec.order,
            "schema": spec.json_schema(),
        }
        for spec in all_actions()
    ]


@router.post("/actions/{action_id}/run")
def run_action(action_id: str, params: dict, request: Request) -> dict:
    try:
        spec = get_action(action_id)
    except UnknownActionError as exc:
        raise HTTPException(status_code=404, detail=f"unknown action: {action_id}") from exc

    try:
        spec.params_model.model_validate(params)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc

    job = request.app.state.runner.submit(action_id, params)
    return job.model_dump()
```

- [x] **Step 4: Write the job routes**

Create `photolib/api/routes_jobs.py`:

```python
"""Job history, detail, and the live event stream."""

from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["jobs"])
POLL_INTERVAL = 0.25


@router.get("/jobs")
def list_jobs(request: Request, limit: int = 50) -> list[dict]:
    return [job.model_dump() for job in request.app.state.jobs.list(limit)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job.model_dump()


@router.get("/jobs/{job_id}/events")
def get_events(job_id: str, request: Request, after: int = 0) -> list[dict]:
    if request.app.state.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")
    return [e.model_dump() for e in request.app.state.jobs.events(job_id, after)]


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    jobs = request.app.state.jobs
    broker = request.app.state.broker
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")

    subscription = broker.subscribe(job_id)

    async def publisher():
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = subscription.get_nowait()
                except queue.Empty:
                    job = jobs.get(job_id)
                    if job and job.status in {"done", "failed", "cancelled"}:
                        yield {"event": "end", "data": job.model_dump_json()}
                        return
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                yield {"event": "message", "data": _to_json(payload)}
        finally:
            broker.unsubscribe(job_id, subscription)

    return EventSourceResponse(publisher())


def _to_json(payload: dict) -> str:
    import json

    return json.dumps(payload)
```

- [x] **Step 5: Write the uvicorn entry point**

Create `photolib/main.py`:

```python
"""Entry point: `uv run uvicorn photolib.main:app --reload`."""

from photolib.api.app import create_app

app = create_app()
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_actions.py tests/test_api_jobs.py -v`
Expected: 9 passed

- [x] **Step 7: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests pass

- [x] **Step 8: Commit**

```bash
git add photolib/api/routes_actions.py photolib/api/routes_jobs.py photolib/main.py tests/test_api_actions.py tests/test_api_jobs.py
git commit -m "feat: action and job routes with sse progress streaming"
```

---

## Task 13: Frontend scaffold, API client, and application shell

**Files:**
- Create: `web/` (via Vite scaffold)
- Create: `web/vite.config.ts` (replace generated)
- Create: `web/src/api/types.ts`
- Create: `web/src/api/client.ts`
- Create: `web/src/components/Nav.tsx`
- Create: `web/src/App.tsx` (replace generated)
- Create: `web/src/main.tsx` (replace generated)
- Create: `web/src/styles.css`
- Create: `web/src/api/client.test.ts`
- Create: `web/src/pages/SettingsPage.tsx` (stub, replaced in Task 14)
- Create: `web/src/pages/ActionPage.tsx` (stub, replaced in Task 15)
- Create: `web/src/pages/JobsPage.tsx` (stub, replaced in Task 15)

**Interfaces:**
- Consumes: the `/api` surface from Tasks 11 and 12.
- Produces:
  - `web/src/api/types.ts`: `FolderRef`, `Settings`, `DriveFolder`, `ActionSpec`, `Job`, `JobEvent`.
  - `web/src/api/client.ts`: `getSettings()`, `putSetting(key, folder)`, `listFolders(parent?)`, `listActions()`, `runAction(id, params)`, `listJobs()`, `getJob(id)`, `getJobEvents(id, after?)`, `streamJob(id, onMessage, onEnd) => () => void`.

- [ ] **Step 1: Scaffold the project**

```bash
cd "$(git rev-parse --show-toplevel)"
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install react-router-dom
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom
```

- [ ] **Step 2: Replace `web/vite.config.ts`**

Import `defineConfig` from `vitest/config`, not `vite` — the `vite` version has no
`test` property and `npm run build` type-checks this file.

```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
```

- [ ] **Step 3: Add the test script to `web/package.json`**

Add to the `"scripts"` object, keeping the generated entries:

```json
"test": "vitest run"
```

- [ ] **Step 4: Write the failing test**

Create `web/src/api/client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSettings, listFolders, runAction } from './client'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('api client', () => {
  it('fetches settings from /api/settings', async () => {
    const fetchMock = stubFetch({ photos_root: null, zip_source: null, credentials_configured: true })
    const settings = await getSettings()
    expect(fetchMock).toHaveBeenCalledWith('/api/settings', expect.anything())
    expect(settings.credentials_configured).toBe(true)
  })

  it('passes the parent folder as a query parameter', async () => {
    const fetchMock = stubFetch({ parent: { id: 'zips', name: 'Z' }, folders: [] })
    await listFolders('zips')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/drive/folders?parent=zips')
  })

  it('defaults the parent to root', async () => {
    const fetchMock = stubFetch({ parent: { id: 'root', name: 'My Drive' }, folders: [] })
    await listFolders()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/drive/folders?parent=root')
  })

  it('posts params when running an action', async () => {
    const fetchMock = stubFetch({ id: 'j1', action: 'check_connection', status: 'queued' })
    await runAction('check_connection', {})
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/actions/check_connection/run')
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{}')
  })

  it('throws on a failed response', async () => {
    stubFetch({ detail: 'boom' }, false, 500)
    await expect(getSettings()).rejects.toThrow(/boom/)
  })
})
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./client`

- [ ] **Step 6: Write the types**

Create `web/src/api/types.ts`:

```ts
export interface FolderRef {
  id: string
  name: string
}

export interface Settings {
  photos_root: FolderRef | null
  zip_source: FolderRef | null
  credentials_configured: boolean
}

export interface DriveFolder {
  id: string
  name: string
  mimeType: string
}

export interface ActionSpec {
  id: string
  title: string
  description: string
  order: number
  schema: { type: string; properties?: Record<string, unknown> }
}

export interface Job {
  id: string
  action: string
  params: Record<string, unknown>
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  message: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface JobEvent {
  id: number
  job_id: string
  ts: number
  level: string
  message: string
}
```

- [ ] **Step 7: Write the client**

Create `web/src/api/client.ts`:

```ts
import type { ActionSpec, DriveFolder, FolderRef, Job, JobEvent, Settings } from './types'

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${response.status}: ${body}`)
  }
  return (await response.json()) as T
}

export const getSettings = () => request<Settings>('/api/settings')

export const putSetting = (key: string, folder: FolderRef) =>
  request<FolderRef>(`/api/settings/${key}`, {
    method: 'PUT',
    body: JSON.stringify(folder),
  })

export const listFolders = (parent = 'root') =>
  request<{ parent: FolderRef; folders: DriveFolder[] }>(
    `/api/drive/folders?parent=${encodeURIComponent(parent)}`,
  )

export const listActions = () => request<ActionSpec[]>('/api/actions')

export const runAction = (id: string, params: Record<string, unknown>) =>
  request<Job>(`/api/actions/${id}/run`, {
    method: 'POST',
    body: JSON.stringify(params),
  })

export const listJobs = () => request<Job[]>('/api/jobs')

export const getJob = (id: string) => request<Job>(`/api/jobs/${id}`)

export const getJobEvents = (id: string, after = 0) =>
  request<JobEvent[]>(`/api/jobs/${id}/events?after=${after}`)

export function streamJob(
  id: string,
  onMessage: (payload: Record<string, unknown>) => void,
  onEnd: () => void,
): () => void {
  const source = new EventSource(`/api/jobs/${id}/stream`)
  source.addEventListener('message', (event) => onMessage(JSON.parse(event.data)))
  source.addEventListener('end', () => {
    source.close()
    onEnd()
  })
  source.onerror = () => {
    source.close()
    onEnd()
  }
  return () => source.close()
}
```

- [ ] **Step 8: Write the shell and navigation**

Create `web/src/components/Nav.tsx`:

```tsx
import { NavLink } from 'react-router-dom'
import type { ActionSpec } from '../api/types'

export function Nav({ actions }: { actions: ActionSpec[] }) {
  return (
    <nav className="nav">
      <h1>Photo Library</h1>
      <section>
        <h2>Setup</h2>
        <NavLink to="/settings">Settings</NavLink>
      </section>
      <section>
        <h2>Actions</h2>
        {actions.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
      <section>
        <h2>Activity</h2>
        <NavLink to="/jobs">Jobs</NavLink>
      </section>
    </nav>
  )
}
```

Create `web/src/App.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { listActions } from './api/client'
import type { ActionSpec } from './api/types'
import { Nav } from './components/Nav'
import { ActionPage } from './pages/ActionPage'
import { JobsPage } from './pages/JobsPage'
import { SettingsPage } from './pages/SettingsPage'

export default function App() {
  const [actions, setActions] = useState<ActionSpec[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listActions().then(setActions).catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="layout">
      <Nav actions={actions} />
      <main>
        {error && <p className="error">{error}</p>}
        <Routes>
          <Route path="/" element={<Navigate to="/settings" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/actions/:actionId" element={<ActionPage actions={actions} />} />
          <Route path="/jobs" element={<JobsPage />} />
        </Routes>
      </main>
    </div>
  )
}
```

Create `web/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
```

Create `web/src/styles.css`:

```css
:root {
  color-scheme: light dark;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

body { margin: 0; }

.layout { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }

.nav {
  padding: 1rem;
  border-right: 1px solid rgba(128, 128, 128, 0.3);
}

.nav h1 { font-size: 1.1rem; }
.nav h2 { font-size: 0.75rem; text-transform: uppercase; opacity: 0.6; margin: 1.25rem 0 0.4rem; }
.nav a { display: block; padding: 0.35rem 0; text-decoration: none; color: inherit; }
.nav a.active { font-weight: 600; text-decoration: underline; }

main { padding: 1.5rem 2rem; max-width: 60rem; }

.error { color: #c0392b; }
.warn { color: #b8860b; }

.card {
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
}

.log {
  font-family: ui-monospace, monospace;
  font-size: 0.8rem;
  max-height: 20rem;
  overflow-y: auto;
  background: rgba(128, 128, 128, 0.08);
  padding: 0.75rem;
  border-radius: 6px;
}

progress { width: 100%; }

.picker-list { list-style: none; padding: 0; max-height: 20rem; overflow-y: auto; }
.picker-list li button { width: 100%; text-align: left; padding: 0.4rem; }

.modal-backdrop {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.4);
  display: grid; place-items: center;
}
.modal { background: Canvas; padding: 1.25rem; border-radius: 10px; width: 32rem; }
```

- [ ] **Step 9: Delete the generated files Vite created that are now unused**

```bash
cd "$(git rev-parse --show-toplevel)/web"
rm -f src/App.css src/index.css src/assets/react.svg
```

- [ ] **Step 10: Create placeholder pages so the build stays green**

`App.tsx` routes to three pages that Tasks 14 and 15 implement. Create minimal
stubs now so this task ends with a compiling build; the later tasks replace
these files wholesale.

Create `web/src/pages/SettingsPage.tsx`:

```tsx
export function SettingsPage() {
  return <p>Settings — implemented in Task 14.</p>
}
```

Create `web/src/pages/ActionPage.tsx`:

```tsx
import type { ActionSpec } from '../api/types'

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  return <p>Actions ({actions.length}) — implemented in Task 15.</p>
}
```

Create `web/src/pages/JobsPage.tsx`:

```tsx
export function JobsPage() {
  return <p>Jobs — implemented in Task 15.</p>
}
```

- [ ] **Step 11: Run the tests and the build to verify they pass**

Run: `cd web && npm test && npm run build`
Expected: 5 tests passed, and the build succeeds with no TypeScript errors.

- [ ] **Step 12: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add web
git commit -m "feat: react scaffold, typed api client, application shell"
```

---

## Task 14: Folder picker and Settings page

**Files:**
- Create: `web/src/components/FolderPicker.tsx`
- Replace: `web/src/pages/SettingsPage.tsx` (overwrite the Task 13 stub)
- Create: `web/src/components/FolderPicker.test.tsx`

**Interfaces:**
- Consumes: `listFolders`, `getSettings`, `putSetting`, `FolderRef`, `DriveFolder`.
- Produces: `FolderPicker({ title, onSelect, onCancel })` — a modal that browses Drive folders from `root`, supports drilling in and going back, and calls `onSelect(folder: FolderRef)` when the user confirms the folder they are currently viewing.

- [ ] **Step 1: Write the failing test**

Create `web/src/components/FolderPicker.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FolderPicker } from './FolderPicker'

vi.mock('../api/client', () => ({
  listFolders: vi.fn(async (parent: string) => {
    if (parent === 'root') {
      return {
        parent: { id: 'root', name: 'My Drive' },
        folders: [{ id: 'zips', name: 'zip-3-22-26', mimeType: 'folder' }],
      }
    }
    return {
      parent: { id: 'zips', name: 'zip-3-22-26' },
      folders: [{ id: 'nested', name: 'Takeout', mimeType: 'folder' }],
    }
  }),
}))

afterEach(() => vi.clearAllMocks())

describe('FolderPicker', () => {
  it('lists folders from the Drive root', async () => {
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(await screen.findByText('zip-3-22-26')).toBeTruthy()
  })

  it('drills into a folder when clicked', async () => {
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={vi.fn()} />)
    await userEvent.click(await screen.findByText('zip-3-22-26'))
    expect(await screen.findByText('Takeout')).toBeTruthy()
  })

  it('selects the folder currently being viewed', async () => {
    const onSelect = vi.fn()
    render(<FolderPicker title="Pick" onSelect={onSelect} onCancel={vi.fn()} />)
    await userEvent.click(await screen.findByText('zip-3-22-26'))
    await waitFor(() => screen.getByText('Takeout'))
    await userEvent.click(screen.getByRole('button', { name: /use this folder/i }))
    expect(onSelect).toHaveBeenCalledWith({ id: 'zips', name: 'zip-3-22-26' })
  })

  it('cancels', async () => {
    const onCancel = vi.fn()
    render(<FolderPicker title="Pick" onSelect={vi.fn()} onCancel={onCancel} />)
    await userEvent.click(await screen.findByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Install the interaction testing library**

```bash
cd "$(git rev-parse --show-toplevel)/web"
npm install -D @testing-library/user-event
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./FolderPicker`

- [ ] **Step 4: Write the folder picker**

Create `web/src/components/FolderPicker.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { listFolders } from '../api/client'
import type { DriveFolder, FolderRef } from '../api/types'

interface Props {
  title: string
  onSelect: (folder: FolderRef) => void
  onCancel: () => void
}

export function FolderPicker({ title, onSelect, onCancel }: Props) {
  const [current, setCurrent] = useState<FolderRef>({ id: 'root', name: 'My Drive' })
  const [folders, setFolders] = useState<DriveFolder[]>([])
  const [trail, setTrail] = useState<FolderRef[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listFolders(current.id)
      .then((result) => {
        if (cancelled) return
        setFolders(result.folders)
        setCurrent(result.parent)
        setError(null)
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id])

  const enter = (folder: DriveFolder) => {
    setTrail([...trail, current])
    setCurrent({ id: folder.id, name: folder.name })
  }

  const goBack = () => {
    const previous = trail[trail.length - 1]
    if (!previous) return
    setTrail(trail.slice(0, -1))
    setCurrent(previous)
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3>{title}</h3>
        <p>
          <strong>{current.name}</strong>
          {trail.length > 0 && (
            <button onClick={goBack} style={{ marginLeft: '0.5rem' }}>
              Back
            </button>
          )}
        </p>
        {error && <p className="error">{error}</p>}
        {loading && <p>Loading…</p>}
        <ul className="picker-list">
          {folders.map((folder) => (
            <li key={folder.id}>
              <button onClick={() => enter(folder)}>{folder.name}</button>
            </li>
          ))}
          {!loading && folders.length === 0 && <li>No subfolders</li>}
        </ul>
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <button onClick={onCancel}>Cancel</button>
          <button onClick={() => onSelect(current)} disabled={current.id === 'root'}>
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Write the Settings page**

Create `web/src/pages/SettingsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getSettings, putSetting } from '../api/client'
import type { FolderRef, Settings } from '../api/types'
import { FolderPicker } from '../components/FolderPicker'

const FIELDS = [
  {
    key: 'photos_root',
    label: 'Global Photos folder',
    help: 'Where organised photos will be placed, in YYYY-MM subfolders.',
  },
  {
    key: 'zip_source',
    label: 'ZIP source folder',
    help: 'The Drive folder holding your Takeout archives.',
  },
] as const

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [picking, setPicking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = () => getSettings().then(setSettings).catch((e) => setError(String(e)))

  useEffect(() => {
    reload()
  }, [])

  const choose = async (key: string, folder: FolderRef) => {
    setPicking(null)
    try {
      await putSetting(key, folder)
      await reload()
    } catch (e) {
      setError(String(e))
    }
  }

  if (!settings) return <p>Loading…</p>

  return (
    <>
      <h2>Settings</h2>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <strong>Drive credentials</strong>
        <p>
          {settings.credentials_configured
            ? 'Authorised — token.json found.'
            : 'Not authorised — token.json is missing from the project root.'}
        </p>
      </div>

      {FIELDS.map((field) => {
        const value = settings[field.key]
        return (
          <div className="card" key={field.key}>
            <strong>{field.label}</strong>
            <p>{field.help}</p>
            <p>{value ? `${value.name} (${value.id})` : <em>Not configured</em>}</p>
            <button onClick={() => setPicking(field.key)}>
              {value ? 'Change' : 'Choose folder'}
            </button>
          </div>
        )
      })}

      {picking && (
        <FolderPicker
          title={FIELDS.find((f) => f.key === picking)!.label}
          onSelect={(folder) => choose(picking, folder)}
          onCancel={() => setPicking(null)}
        />
      )}
    </>
  )
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: 9 passed (5 client + 4 picker)

- [ ] **Step 7: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add web/src
git commit -m "feat: drive folder picker and settings page"
```

---

## Task 15: Action pages and job history

**Files:**
- Create: `web/src/components/JobProgress.tsx`
- Replace: `web/src/pages/ActionPage.tsx` (overwrite the Task 13 stub)
- Replace: `web/src/pages/JobsPage.tsx` (overwrite the Task 13 stub)
- Create: `web/src/pages/ActionPage.test.tsx`

**Interfaces:**
- Consumes: `runAction`, `getJob`, `getJobEvents`, `streamJob`, `listJobs`, `ActionSpec`, `Job`, `JobEvent`.
- Produces: `JobProgress({ jobId })` — subscribes to the SSE stream, renders a progress bar and a scrolling log, and stops on completion. `ActionPage({ actions })` — renders the action named in the route with a Run button. `JobsPage()` — a table of recent jobs.

Phase 1 actions take no parameters, so the page renders a Run button rather than a
generated form; the parameter schema is displayed so later actions have an obvious
place to grow into.

- [ ] **Step 1: Write the failing test**

Create `web/src/pages/ActionPage.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionPage } from './ActionPage'

const runAction = vi.fn(async () => ({ id: 'job-1', status: 'queued' }))

vi.mock('../api/client', () => ({
  runAction: (...args: unknown[]) => runAction(...(args as [string, object])),
  getJob: vi.fn(async () => ({ id: 'job-1', status: 'done', progress: 1, message: null })),
  getJobEvents: vi.fn(async () => []),
  streamJob: vi.fn(() => () => undefined),
}))

const ACTIONS = [
  {
    id: 'check_connection',
    title: 'Check Connection',
    description: 'Verify Drive access.',
    order: 0,
    schema: { type: 'object', properties: {} },
  },
]

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/actions/:actionId" element={<ActionPage actions={ACTIONS} />} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => vi.clearAllMocks())

describe('ActionPage', () => {
  it('shows the action title and description', () => {
    renderAt('/actions/check_connection')
    expect(screen.getByText('Check Connection')).toBeTruthy()
    expect(screen.getByText('Verify Drive access.')).toBeTruthy()
  })

  it('reports an unknown action', () => {
    renderAt('/actions/nope')
    expect(screen.getByText(/unknown action/i)).toBeTruthy()
  })

  it('runs the action when the button is clicked', async () => {
    renderAt('/actions/check_connection')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(runAction).toHaveBeenCalledWith('check_connection', {})
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./ActionPage`

- [ ] **Step 3: Write the progress component**

Create `web/src/components/JobProgress.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { getJob, getJobEvents, streamJob } from '../api/client'
import type { Job, JobEvent } from '../api/types'

export function JobProgress({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let stopped = false

    const refresh = async () => {
      const [current, log] = await Promise.all([getJob(jobId), getJobEvents(jobId)])
      if (stopped) return
      setJob(current)
      setEvents(log)
    }

    refresh()
    const close = streamJob(jobId, refresh, refresh)
    return () => {
      stopped = true
      close()
    }
  }, [jobId])

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight)
  }, [events])

  if (!job) return <p>Starting…</p>

  return (
    <div className="card">
      <p>
        Status: <strong>{job.status}</strong>
        {job.message && ` — ${job.message}`}
      </p>
      <progress value={job.progress} max={1} />
      {job.error && <pre className="error">{job.error}</pre>}
      <div className="log" ref={logRef}>
        {events.map((event) => (
          <div key={event.id} className={event.level}>
            {event.message}
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Write the action page**

Create `web/src/pages/ActionPage.tsx`:

```tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { runAction } from '../api/client'
import type { ActionSpec } from '../api/types'
import { JobProgress } from '../components/JobProgress'

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  const { actionId } = useParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const action = actions.find((a) => a.id === actionId)
  if (!action) return <p>Unknown action: {actionId}</p>

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const job = await runAction(action.id, {})
      setJobId(job.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const hasParams = Object.keys(action.schema.properties ?? {}).length > 0

  return (
    <>
      <h2>{action.title}</h2>
      <p>{action.description}</p>
      {hasParams && (
        <pre className="log">{JSON.stringify(action.schema, null, 2)}</pre>
      )}
      <button onClick={start} disabled={busy}>
        {busy ? 'Starting…' : 'Run'}
      </button>
      {error && <p className="error">{error}</p>}
      {jobId && <JobProgress jobId={jobId} />}
    </>
  )
}
```

- [ ] **Step 5: Write the jobs page**

Create `web/src/pages/JobsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { listJobs } from '../api/client'
import type { Job } from '../api/types'
import { JobProgress } from '../components/JobProgress'

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = () => listJobs().then(setJobs).catch((e) => setError(String(e)))
    load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  return (
    <>
      <h2>Jobs</h2>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Action</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Started</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>{job.action}</td>
              <td>{job.status}</td>
              <td>{Math.round(job.progress * 100)}%</td>
              <td>{job.started_at ?? '—'}</td>
              <td>
                <button onClick={() => setSelected(job.id)}>Details</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {jobs.length === 0 && <p>No jobs have been run yet.</p>}
      {selected && <JobProgress jobId={selected} />}
    </>
  )
}
```

- [ ] **Step 6: Run the frontend tests**

Run: `cd web && npm test`
Expected: 12 passed

- [ ] **Step 7: Verify the production build compiles**

Run: `cd web && npm run build`
Expected: build succeeds with no TypeScript errors

- [ ] **Step 8: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add web/src
git commit -m "feat: action pages with live progress and job history"
```

---

## Task 16: Live verification and documentation

Proves the foundation works against the real archives, and documents how to run
the app.

**Files:**
- Create: `tests/test_live_drive.py`
- Create: `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: an opt-in live test suite (`uv run pytest -m live`) and developer documentation.

- [ ] **Step 1: Write the opt-in live test**

Create `tests/test_live_drive.py`:

```python
"""Opt-in tests against the real Drive account.

Run with: uv run pytest -m live -v
These are excluded from the default suite by the addopts in pyproject.toml.
"""

from __future__ import annotations

import pytest

from photolib import archives
from photolib.config import Config
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient

ZIP_FOLDER_ID = "1y2pqVRWi92920usgc7Yy1qe-81hqUljO"

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    cfg = Config.load()
    if not cfg.token_path.exists():
        pytest.skip("token.json not present")
    drive = DriveClient(TokenProvider(cfg.credentials_path, cfg.token_path))
    yield drive
    drive.close()


def test_can_refresh_token_and_read_folder(client):
    folder = client.get_file(ZIP_FOLDER_ID)
    assert folder.is_folder


def test_zip_source_contains_seventeen_archives(client):
    children = client.list_children(ZIP_FOLDER_ID)
    zips = [c for c in children if c.name.lower().endswith(".zip")]
    assert len(zips) == 17


def test_reads_a_real_archive_index_over_ranges(client):
    zips = [
        c
        for c in client.list_children(ZIP_FOLDER_ID)
        if c.name.lower().endswith(".zip")
    ]
    first = sorted(zips, key=lambda f: f.name)[0]
    entries = archives.list_archive_entries(client, first.id, first.size)
    assert len(entries) > 100
    assert any(e.name.endswith(".supplemental-metadata.json") for e in entries)
    assert any(e.path.startswith("Takeout/Google Photos/") for e in entries)


def test_extracts_one_sidecar_and_verifies_its_crc(client):
    import json

    zips = [
        c
        for c in client.list_children(ZIP_FOLDER_ID)
        if c.name.lower().endswith(".zip")
    ]
    first = sorted(zips, key=lambda f: f.name)[0]
    entries = archives.list_archive_entries(client, first.id, first.size)
    sidecar = next(e for e in entries if e.path.lower().endswith(".json"))
    payload = json.loads(archives.extract_from_archive(client, first.id, sidecar))
    assert "title" in payload
```

- [ ] **Step 2: Run the live tests**

Run: `uv run pytest -m live -v`
Expected: 4 passed. Each reads only a few kilobytes; no archive is downloaded whole.

- [ ] **Step 3: Write the README**

Create `README.md`:

````markdown
# Photo Library Organizer

Extracts a Google Photos Takeout export stored as ZIP archives in Google Drive
into a single organised photo library, arranged by month, with duplicates
skipped and tags applied.

See `docs/superpowers/specs/2026-08-09-google-photos-organizer-design.md` for the
full design and the measurements it is based on.

## Requirements

- Python 3.12, installed and managed via [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- `credentials.json` and `token.json` in the project root, authorised for the
  `https://www.googleapis.com/auth/drive` scope. Both are gitignored and must
  never be committed.

## Setup

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
cd web && npm install && cd ..
```

## Running

Two processes in development:

```bash
# Terminal 1 — API on http://127.0.0.1:8000
uv run uvicorn photolib.main:app --reload

# Terminal 2 — UI on http://localhost:5173
cd web && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend.

## Tests

```bash
uv run pytest              # backend, offline, no network
uv run pytest -m live      # opt-in, hits the real Drive account
cd web && npm test         # frontend
```

The default backend suite never touches the network. Drive behaviour is covered
by `tests/fakes/fake_drive.py`, and ZIP behaviour by archives built in memory.

## Architecture

- `photolib/drive/` — OAuth token refresh and a REST client over `httpx`
- `photolib/ziparchive/` — reads ZIP indexes and extracts single entries using
  HTTP byte ranges, so a 2.15 GB archive is never downloaded to retrieve one photo
- `photolib/db/` — SQLite catalog holding settings, archive indexes, and jobs
- `photolib/actions/` — one module per capability; each becomes a page in the UI
- `photolib/jobs/` — a background worker that runs actions and streams progress
- `photolib/api/` — FastAPI routes
- `web/` — React + Vite frontend

## Adding an action

Create a module in `photolib/actions/` declaring `ID`, `TITLE`, `DESCRIPTION`,
`ORDER`, a `Params` model extending `ActionParams`, and a `run(ctx, params)` generator that
yields `ProgressEvent`s. The registry discovers it automatically and the
frontend renders a page for it — no frontend changes required.
````

- [ ] **Step 4: Manual end-to-end verification**

Start both processes as described in the README, then confirm each of these in
the browser:

1. http://localhost:5173 redirects to Settings.
2. Settings reports **"Authorised — token.json found."**
3. "Choose folder" on the Global Photos folder opens the picker showing your real
   Drive folders.
4. Drilling into a folder lists its subfolders; Back returns to the parent.
5. Selecting a folder stores it, and it survives a page reload.
6. Repeat for the ZIP source folder, selecting `zip-3-22-26`.
7. The sidebar lists **Check Connection** under Actions.
8. Running it streams log lines live and finishes with status `done`, reporting
   the Global Photos folder and **17 archive(s)** in the ZIP source folder.
9. The Jobs page lists the run; Details replays its log.

- [ ] **Step 5: Run the full suite one final time**

```bash
uv run pytest -v
cd web && npm test && npm run build
```

Expected: all backend tests pass, all frontend tests pass, the build succeeds.

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_live_drive.py
git commit -m "docs: readme and opt-in live drive verification"
```

---

## Done criteria

Phase 1 is complete when:

- `uv run pytest` passes with no network access.
- `uv run pytest -m live` passes against the real Drive account.
- `cd web && npm test && npm run build` passes.
- The app runs, both folders can be configured through the UI, and **Check
  Connection** reports 17 archives with live streaming progress.

Phase 2 then adds `scan_archives`, `pair_metadata`, and `plan_organize` as three
new modules in `photolib/actions/`, plus a Review page — no changes to the
foundation required.

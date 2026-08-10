# Phase 4: Browse and Tag — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the organised library a face — a thumbnail grid you can filter and browse, manual tags you can apply in bulk, and one explicit action that mirrors those tags onto Drive.

**Architecture:** The Library browses `drive_files` (rebuilt by Scan) left-joined to `media` on `media.drive_file_id = drive_files.drive_id`, so files this tool never uploaded still appear — just without capture time or place. Tags are manual only and key on Drive's own file id, which survives a re-scan; month, place, country, type, and duplicate status are derived from columns at query time rather than stored as tag rows. Thumbnails are proxied from Drive's `thumbnailLink` through a disk cache, because Chrome cannot render the 591 HEIC files.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx, Pydantic v2 on the backend. React 19, react-router 7, Vite, Vitest on the frontend. No new dependencies in either.

## Global Constraints

- **No new dependencies.** Neither `pyproject.toml` nor `web/package.json` gains an entry. The grid is hand-rolled; there is no virtualisation library.
- **Tags are manual only.** There is no `retag` action and no auto-tag rows. `place:`, `country:`, `year:`, `month:`, `device:`, `archive:` are *derived filters over existing columns*, never rows in `tags`.
- **There is no Duplicates page.** Duplicates are one filter chip on the Library page.
- **Tags key on `drive_files.drive_id` (TEXT), never on `drive_files.id`.** Scan rewrites that autoincrement id.
- **Only `sync_tags` may write to Drive.** Every other new endpoint is read-only or SQLite-only.
- **`sync_tags` reports by default and requires `confirm=true` to act**, matching `clear_stale_trees`.
- Tag slugs are capped at **60 characters** so `t_<slug>` fits Drive's 124-byte key+value limit.
- Drive allows **30 appProperties per file**; Organize already writes ~5, so warn above **25 tags**.
- The default `pytest` run must stay offline. Anything hitting real Drive is marked `@pytest.mark.live`.
- Backend tests: `uv run pytest`. Frontend tests: `cd web && npm test`.
- Existing style: `from __future__ import annotations` at the top of every module, module docstrings that say *why*, repos take a `sqlite3.Connection`, routes read `request.app.state.conn`.

---

## File Structure

**Backend — create**

| File | Responsibility |
| --- | --- |
| `photolib/db/tags_repo.py` | Every read and write of `tags` and `file_tags`. Slugging lives here. |
| `photolib/db/library_repo.py` | The filtered library query, its facet counts, and the id list behind "select all matching". |
| `photolib/thumbs.py` | Disk-backed thumbnail cache. Knows nothing about HTTP routing. |
| `photolib/api/routes_library.py` | `/api/library/*` |
| `photolib/api/routes_tags.py` | `/api/tags/*` |
| `photolib/api/routes_thumbs.py` | `/api/thumb/{drive_id}` |
| `photolib/actions/sync_tags.py` | The one mutating action: mirror catalog tags onto Drive `appProperties`. |

**Backend — modify**

| File | Change |
| --- | --- |
| `photolib/db/schema.sql` | `tags`, `file_tags`; `drive_files.mime_type`, `drive_files.synced_tags` |
| `photolib/db/migrations.py` | `SCHEMA_VERSION = 4`, two added columns |
| `photolib/db/scan_repo.py` | `replace_drive_files` → `upsert_drive_files` (preserves tags) |
| `photolib/actions/scan_archives.py` | Record `mime_type` when indexing the destination |
| `photolib/drive/client.py` | `thumbnailLink` in `FILE_FIELDS`; `fetch_thumbnail`, `app_properties` |
| `photolib/drive/writer.py` | `update_properties` |
| `photolib/api/app.py` | Build the thumbnail cache; include three routers |

**Frontend — create**

| File | Responsibility |
| --- | --- |
| `web/src/lib/selection.ts` | Pure click/shift-click/⌘-click selection maths. No React. |
| `web/src/lib/filters.ts` | The filter shape and its conversion to query params. No React. |
| `web/src/components/FilterSidebar.tsx` | Facet lists and the filter chips |
| `web/src/components/Lightbox.tsx` | Single-file view, metadata, per-file tag editing |
| `web/src/components/TagPicker.tsx` | The bulk add/remove-tag toolbar |
| `web/src/pages/LibraryPage.tsx` | Grid, month grouping, selection wiring |
| `web/src/pages/TagsPage.tsx` | Tag CRUD, merge, counts |

**Frontend — modify:** `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/App.tsx`, `web/src/components/Nav.tsx`, `web/src/styles.css`.

**Tests — create:** `tests/test_tags_repo.py`, `tests/test_library_repo.py`, `tests/test_thumbs.py`, `tests/test_api_library.py`, `tests/test_api_tags.py`, `tests/test_api_thumbs.py`, `tests/test_action_sync_tags.py`, `tests/test_live_phase4.py`, `web/src/lib/selection.test.ts`, `web/src/lib/filters.test.ts`, `web/src/pages/LibraryPage.test.tsx`, `web/src/pages/TagsPage.test.tsx`.

**Tests — modify:** `tests/test_migrations.py`, `tests/test_scan_repo.py`, `tests/fakes/fake_drive.py`.

---

## Task 1: Schema and migration to v4

**Files:**
- Modify: `photolib/db/schema.sql:99-117`
- Modify: `photolib/db/migrations.py:17-27`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `tags(id, name, slug, color)` and `file_tags(drive_id, tag_id)`; columns `drive_files.mime_type TEXT` and `drive_files.synced_tags TEXT`; `migrations.SCHEMA_VERSION == 4`.

Two notes on why the schema looks like this. `file_tags` has **no foreign key to `drive_files`** on purpose: Scan rebuilds that table, and a cascade would wipe every tag. Orphan rows are simply retained and excluded from counts. `synced_tags` records the slug list last written to Drive, which is what lets `sync_tags` (Task 11) find a file whose tags were *removed* — a file with zero catalog tags would otherwise never be visited, and its stale `t_*` property would live on Drive forever.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_migrations.py` entirely:

```python
"""A catalog created fresh and a catalog upgraded in place must end identical."""

import sqlite3

from photolib.db import catalog, migrations

V3_COLUMNS = (
    ("media", "upload_session_uri"),
    ("media", "upload_offset"),
    ("media", "session_started_at"),
    ("media", "attempts"),
    ("drive_files", "trashed_at"),
)
V4_COLUMNS = (
    ("drive_files", "mime_type"),
    ("drive_files", "synced_tags"),
)


def _schema(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Every (table, column) pair in the database."""
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        (table, row["name"])
        for table in tables
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def test_version_is_four(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    assert migrations.SCHEMA_VERSION == 4
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_media_has_the_upload_session_columns(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    assert {
        "upload_session_uri", "upload_offset", "session_started_at", "attempts"
    } <= columns


def test_drive_files_carries_mime_type_and_sync_state(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(drive_files)")}
    assert {"mime_type", "synced_tags"} <= columns


def test_tag_tables_exist(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    tags = {row["name"] for row in conn.execute("PRAGMA table_info(tags)")}
    file_tags = {row["name"] for row in conn.execute("PRAGMA table_info(file_tags)")}
    assert {"id", "name", "slug", "color"} <= tags
    assert {"drive_id", "tag_id"} <= file_tags


def test_upgrading_a_v2_catalog_matches_a_fresh_one(tmp_path):
    """The bug this guards: 'created new' and 'upgraded' drifting apart."""
    old = tmp_path / "old.db"
    conn = catalog.connect(old)
    conn.execute("INSERT INTO settings (key, value) VALUES ('photos_root', 'x')")
    conn.commit()
    # Strip everything added after v2 and rewind the version: a genuine v2 catalog.
    for table, column in V3_COLUMNS + V4_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DROP TABLE file_tags")
    conn.execute("DROP TABLE tags")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    upgraded = catalog.connect(old)
    fresh = catalog.connect(tmp_path / "fresh.db")

    assert _schema(upgraded) == _schema(fresh)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 4
    assert upgraded.execute("SELECT value FROM settings").fetchone()["value"] == "x"


def test_upgrading_keeps_existing_tags(tmp_path):
    """Migration must never be a data-loss event for hand-made tags."""
    db = tmp_path / "t.db"
    conn = catalog.connect(db)
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('drive-1', 1)")
    conn.commit()
    conn.close()

    upgraded = catalog.connect(db)
    assert upgraded.execute("SELECT slug FROM tags").fetchone()["slug"] == "family"
    assert upgraded.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 1


def test_migrating_twice_is_harmless(tmp_path):
    db = tmp_path / "t.db"
    catalog.connect(db).close()
    conn = catalog.connect(db)
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL. `test_version_is_four` asserts `4 == 3`; `test_tag_tables_exist` gets an empty set from `PRAGMA table_info(tags)` because no such table exists.

- [ ] **Step 3: Add the tables to `schema.sql`**

Append to `photolib/db/schema.sql`, after the `geocache` block:

```sql
CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    slug  TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#6b7280'
);

-- No foreign key to drive_files on purpose: Scan rebuilds that table, and a
-- cascade would delete every tag with it. Orphans are retained and excluded
-- from counts instead.
CREATE TABLE IF NOT EXISTS file_tags (
    drive_id TEXT NOT NULL,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (drive_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag_id);
```

And add the two columns to the `drive_files` definition at `schema.sql:99-108`, so a fresh catalog matches an upgraded one:

```sql
CREATE TABLE IF NOT EXISTS drive_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    parent_path TEXT NOT NULL,
    md5         TEXT,
    size        INTEGER,
    indexed_at  TEXT,
    trashed_at  TEXT,
    mime_type   TEXT,
    synced_tags TEXT
);
```

- [ ] **Step 4: Bump the migration**

In `photolib/db/migrations.py`, change `SCHEMA_VERSION` to `4` and extend `_ADDED_COLUMNS`:

```python
SCHEMA_VERSION = 4

# (table, column, full column definition)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("media", "upload_session_uri", "upload_session_uri TEXT"),
    ("media", "upload_offset", "upload_offset INTEGER NOT NULL DEFAULT 0"),
    ("media", "session_started_at", "session_started_at TEXT"),
    ("media", "attempts", "attempts INTEGER NOT NULL DEFAULT 0"),
    ("drive_files", "trashed_at", "trashed_at TEXT"),
    ("drive_files", "mime_type", "mime_type TEXT"),
    ("drive_files", "synced_tags", "synced_tags TEXT"),
)
```

No code change is needed to create the new tables on an old catalog: `migrate` runs `executescript(schema.sql)` first, and both statements are `CREATE TABLE IF NOT EXISTS`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. Nothing else reads these tables yet, so no other test should move.

- [ ] **Step 7: Commit**

```bash
git add photolib/db/schema.sql photolib/db/migrations.py tests/test_migrations.py
git commit -m "feat(db): add tags and file_tags, plus mime_type and sync state on drive_files"
```

---

## Task 2: TagsRepo

**Files:**
- Create: `photolib/db/tags_repo.py`
- Test: `tests/test_tags_repo.py`

**Interfaces:**
- Consumes: the `tags` and `file_tags` tables from Task 1.
- Produces:

```python
DEFAULT_COLOR: str = "#6b7280"
MAX_SLUG = 60

def slugify(name: str) -> str: ...

class TagsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None: ...
    def create(self, name: str, color: str = DEFAULT_COLOR) -> sqlite3.Row: ...
    def list_with_counts(self) -> list[sqlite3.Row]: ...   # id, name, slug, color, file_count
    def get(self, tag_id: int) -> sqlite3.Row | None: ...
    def rename(self, tag_id: int, name: str) -> sqlite3.Row: ...
    def recolor(self, tag_id: int, color: str) -> sqlite3.Row: ...
    def delete(self, tag_id: int) -> None: ...
    def merge(self, source_id: int, target_id: int) -> int: ...      # files moved
    def add_files(self, tag_id: int, drive_ids: list[str]) -> int: ...    # rows added
    def remove_files(self, tag_id: int, drive_ids: list[str]) -> int: ... # rows removed
    def tags_for(self, drive_ids: list[str]) -> dict[str, list[dict]]: ...
    def slugs_by_file(self) -> dict[str, set[str]]: ...
```

`DuplicateTagError` is raised when a name slugs to one already taken.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tags_repo.py`:

```python
import pytest

from photolib.db.tags_repo import DuplicateTagError, TagsRepo, slugify


def _drive_file(conn, drive_id: str, name: str = "IMG.HEIC", parent: str = "2025-05"):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path) VALUES (?, ?, ?)",
        (drive_id, name, parent),
    )
    conn.commit()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Family", "family"),
        ("Greece 2025", "greece-2025"),
        ("  Print These!  ", "print-these"),
        ("Lake  Como", "lake-como"),
        ("Ünïcodé", "unicode"),
        ("a" * 80, "a" * 60),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slug_must_survive_being_a_drive_property_key():
    """t_<slug> shares a 124-byte budget with its value; 60 chars leaves room."""
    assert len(slugify("x" * 200)) == 60


def test_create_returns_the_row(conn):
    tag = TagsRepo(conn).create("Family")
    assert tag["name"] == "Family"
    assert tag["slug"] == "family"
    assert tag["color"] == "#6b7280"


def test_creating_the_same_slug_twice_is_an_error(conn):
    repo = TagsRepo(conn)
    repo.create("Family")
    with pytest.raises(DuplicateTagError):
        repo.create("  family ")


def test_counts_only_files_that_are_still_in_drive(conn):
    """A tag on a file that vanished from Drive must not inflate its count."""
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "here")
    repo.add_files(tag["id"], ["here", "gone"])

    counts = {row["slug"]: row["file_count"] for row in repo.list_with_counts()}
    assert counts["family"] == 1


def test_trashed_files_do_not_count(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    conn.commit()
    repo.add_files(tag["id"], ["d1"])

    assert repo.list_with_counts()[0]["file_count"] == 0


def test_add_files_is_idempotent(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")

    assert repo.add_files(tag["id"], ["d1", "d1"]) == 1
    assert repo.add_files(tag["id"], ["d1"]) == 0
    assert repo.list_with_counts()[0]["file_count"] == 1


def test_remove_files(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    _drive_file(conn, "d2")
    repo.add_files(tag["id"], ["d1", "d2"])

    assert repo.remove_files(tag["id"], ["d1"]) == 1
    assert repo.list_with_counts()[0]["file_count"] == 1


def test_rename_updates_the_slug(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Familly")
    renamed = repo.rename(tag["id"], "Family")
    assert (renamed["name"], renamed["slug"]) == ("Family", "family")


def test_renaming_onto_an_existing_slug_is_an_error(conn):
    repo = TagsRepo(conn)
    repo.create("Family")
    other = repo.create("Friends")
    with pytest.raises(DuplicateTagError):
        repo.rename(other["id"], "Family")


def test_recolor(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    assert repo.recolor(tag["id"], "#ff0000")["color"] == "#ff0000"


def test_delete_takes_its_assignments_with_it(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    repo.add_files(tag["id"], ["d1"])

    repo.delete(tag["id"])
    assert repo.list_with_counts() == []
    assert conn.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 0


def test_merge_moves_files_and_drops_the_source(conn):
    repo = TagsRepo(conn)
    source = repo.create("Familly")
    target = repo.create("Family")
    for drive_id in ("d1", "d2"):
        _drive_file(conn, drive_id)
    repo.add_files(source["id"], ["d1", "d2"])
    repo.add_files(target["id"], ["d2"])

    moved = repo.merge(source["id"], target["id"])

    assert moved == 1                      # d2 was already there
    assert [r["slug"] for r in repo.list_with_counts()] == ["family"]
    assert repo.list_with_counts()[0]["file_count"] == 2


def test_merging_a_tag_into_itself_is_refused(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    with pytest.raises(ValueError):
        repo.merge(tag["id"], tag["id"])


def test_tags_for_groups_by_file(conn):
    repo = TagsRepo(conn)
    family = repo.create("Family")
    print_these = repo.create("Print These")
    for drive_id in ("d1", "d2"):
        _drive_file(conn, drive_id)
    repo.add_files(family["id"], ["d1", "d2"])
    repo.add_files(print_these["id"], ["d1"])

    grouped = repo.tags_for(["d1", "d2", "d3"])

    assert [t["slug"] for t in grouped["d1"]] == ["family", "print-these"]
    assert [t["slug"] for t in grouped["d2"]] == ["family"]
    assert "d3" not in grouped


def test_tags_for_handles_an_empty_request(conn):
    assert TagsRepo(conn).tags_for([]) == {}


def test_tags_for_survives_more_files_than_sqlite_takes_variables(conn):
    """1,284 files in one page must not trip SQLite's variable limit."""
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    ids = [f"d{n}" for n in range(1500)]
    for drive_id in ids:
        _drive_file(conn, drive_id)
    repo.add_files(tag["id"], ids)

    assert len(repo.tags_for(ids)) == 1500


def test_slugs_by_file(conn):
    repo = TagsRepo(conn)
    family = repo.create("Family")
    _drive_file(conn, "d1")
    repo.add_files(family["id"], ["d1"])

    assert repo.slugs_by_file() == {"d1": {"family"}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tags_repo.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'photolib.db.tags_repo'`.

- [ ] **Step 3: Write the implementation**

Create `photolib/db/tags_repo.py`:

```python
"""Manual tags and their assignment to Drive files.

Tags key on Drive's own file id rather than `drive_files.id`, because Scan
rebuilds that table and its autoincrement ids move. That also means a tag
survives the file leaving and returning, and that `file_tags` may hold rows
for files no longer in Drive — those are retained, not pruned, and excluded
from every count.

Every tag here is manual. Place, country, year, month, device and archive are
derived from columns at query time (see `library_repo`), never stored as rows.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

DEFAULT_COLOR = "#6b7280"

# `t_<slug>` and its value share Drive's 124-byte appProperties budget.
MAX_SLUG = 60

# SQLite's default host-parameter ceiling is 999. A page of 1,284 files would
# blow straight through it, so anything taking a list of ids batches.
_BATCH = 400


class DuplicateTagError(Exception):
    """That name already exists, or slugs onto one that does."""


def slugify(name: str) -> str:
    """A URL-safe, Drive-property-safe key for a tag name."""
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower())
    return hyphenated.strip("-")[:MAX_SLUG].strip("-")


def _batched(items: list[str]) -> list[list[str]]:
    return [items[i : i + _BATCH] for i in range(0, len(items), _BATCH)]


class TagsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------- the tags themselves ----------

    def create(self, name: str, color: str = DEFAULT_COLOR) -> sqlite3.Row:
        name = name.strip()
        slug = slugify(name)
        if not slug:
            raise ValueError("a tag name must contain at least one letter or digit")
        try:
            cursor = self._conn.execute(
                "INSERT INTO tags (name, slug, color) VALUES (?, ?, ?)",
                (name, slug, color),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateTagError(f"a tag named '{slug}' already exists") from exc
        self._conn.commit()
        return self.get(cursor.lastrowid)

    def get(self, tag_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()

    def list_with_counts(self) -> list[sqlite3.Row]:
        """Every tag, with how many live Drive files carry it."""
        return list(
            self._conn.execute(
                "SELECT t.id, t.name, t.slug, t.color, "
                "       (SELECT COUNT(*) FROM file_tags ft "
                "          JOIN drive_files d ON d.drive_id = ft.drive_id "
                "         WHERE ft.tag_id = t.id AND d.trashed_at IS NULL) "
                "       AS file_count "
                "FROM tags t ORDER BY t.name COLLATE NOCASE"
            )
        )

    def rename(self, tag_id: int, name: str) -> sqlite3.Row:
        name = name.strip()
        slug = slugify(name)
        if not slug:
            raise ValueError("a tag name must contain at least one letter or digit")
        try:
            self._conn.execute(
                "UPDATE tags SET name = ?, slug = ? WHERE id = ?",
                (name, slug, tag_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateTagError(f"a tag named '{slug}' already exists") from exc
        self._conn.commit()
        return self.get(tag_id)

    def recolor(self, tag_id: int, color: str) -> sqlite3.Row:
        self._conn.execute("UPDATE tags SET color = ? WHERE id = ?", (color, tag_id))
        self._conn.commit()
        return self.get(tag_id)

    def delete(self, tag_id: int) -> None:
        self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._conn.commit()

    def merge(self, source_id: int, target_id: int) -> int:
        """Move every assignment from source to target, then drop source."""
        if source_id == target_id:
            raise ValueError("a tag cannot be merged into itself")
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO file_tags (drive_id, tag_id) "
            "SELECT drive_id, ? FROM file_tags WHERE tag_id = ?",
            (target_id, source_id),
        )
        moved = cursor.rowcount
        self._conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
        self._conn.commit()
        return moved

    # ---------- assignment ----------

    def add_files(self, tag_id: int, drive_ids: list[str]) -> int:
        added = 0
        for batch in _batched(list(dict.fromkeys(drive_ids))):
            cursor = self._conn.executemany(
                "INSERT OR IGNORE INTO file_tags (drive_id, tag_id) VALUES (?, ?)",
                [(drive_id, tag_id) for drive_id in batch],
            )
            added += cursor.rowcount
        self._conn.commit()
        return added

    def remove_files(self, tag_id: int, drive_ids: list[str]) -> int:
        removed = 0
        for batch in _batched(list(dict.fromkeys(drive_ids))):
            placeholders = ",".join("?" * len(batch))
            cursor = self._conn.execute(
                f"DELETE FROM file_tags WHERE tag_id = ? "
                f"AND drive_id IN ({placeholders})",
                [tag_id, *batch],
            )
            removed += cursor.rowcount
        self._conn.commit()
        return removed

    # ---------- reads for other layers ----------

    def tags_for(self, drive_ids: list[str]) -> dict[str, list[dict]]:
        """Tags per file, for the files asked about. Untagged files are absent."""
        grouped: dict[str, list[dict]] = {}
        for batch in _batched(list(dict.fromkeys(drive_ids))):
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"SELECT ft.drive_id, t.id, t.name, t.slug, t.color "
                f"FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
                f"WHERE ft.drive_id IN ({placeholders}) "
                f"ORDER BY t.name COLLATE NOCASE",
                batch,
            )
            for row in rows:
                grouped.setdefault(row["drive_id"], []).append(
                    {
                        "id": row["id"], "name": row["name"],
                        "slug": row["slug"], "color": row["color"],
                    }
                )
        return grouped

    def slugs_by_file(self) -> dict[str, set[str]]:
        """Every assignment in the catalog. `sync_tags` diffs against this."""
        grouped: dict[str, set[str]] = {}
        for row in self._conn.execute(
            "SELECT ft.drive_id, t.slug FROM file_tags ft "
            "JOIN tags t ON t.id = ft.tag_id"
        ):
            grouped.setdefault(row["drive_id"], set()).add(row["slug"])
        return grouped
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tags_repo.py -v`
Expected: PASS, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add photolib/db/tags_repo.py tests/test_tags_repo.py
git commit -m "feat(db): TagsRepo — create, rename, merge, assign, and count manual tags"
```

---

## Task 3: Scan preserves tags and records mime type

**Files:**
- Modify: `photolib/db/scan_repo.py:95-107`
- Modify: `photolib/actions/scan_archives.py:30-53`
- Test: `tests/test_scan_repo.py`

**Interfaces:**
- Consumes: `drive_files.mime_type` from Task 1.
- Produces: `ScanRepo.upsert_drive_files(rows: list[dict]) -> None`, where each row is `{"drive_id", "name", "parent_path", "md5", "size", "mime_type"}`. `replace_drive_files` is gone.

The current `DELETE FROM drive_files` at `scan_repo.py:97` would erase every tag on each Scan, because `file_tags` keys on `drive_id`. Upserting keeps the row — and therefore the tag — while still dropping files that genuinely left Drive. The sweep uses the `indexed_at` stamp rather than `drive_id NOT IN (...)`, which would exceed SQLite's host-parameter limit at 1,284 files and is a syntax error when the folder is empty.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scan_repo.py`:

```python
def _rows(*specs):
    return [
        {
            "drive_id": drive_id, "name": name, "parent_path": parent,
            "md5": "abc", "size": 10, "mime_type": mime,
        }
        for drive_id, name, parent, mime in specs
    ]


def test_upsert_drive_files_inserts(conn):
    ScanRepo(conn).upsert_drive_files(
        _rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic"))
    )
    row = conn.execute("SELECT * FROM drive_files").fetchone()
    assert (row["drive_id"], row["parent_path"], row["mime_type"]) == (
        "d1", "2025-05", "image/heic"
    )
    assert row["indexed_at"] is not None


def test_upsert_drive_files_keeps_tags_across_a_rescan(conn):
    """The bug this guards: re-Scan silently deleting every tag."""
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d1', 1)")
    conn.commit()

    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert conn.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 1


def test_upsert_drive_files_updates_a_moved_file(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-04", "image/heic")))
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    rows = list(conn.execute("SELECT parent_path FROM drive_files"))
    assert len(rows) == 1
    assert rows[0]["parent_path"] == "2025-05"


def test_upsert_drive_files_drops_what_left_drive(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(
        _rows(
            ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
            ("d2", "IMG_2.HEIC", "2025-05", "image/heic"),
        )
    )
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert [r["drive_id"] for r in conn.execute("SELECT drive_id FROM drive_files")] == ["d1"]


def test_upsert_drive_files_with_nothing_clears_the_index(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    repo.upsert_drive_files([])

    assert conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0] == 0


def test_upsert_drive_files_handles_more_rows_than_sqlite_variables(conn):
    rows = _rows(*[(f"d{n}", f"IMG_{n}.HEIC", "2025-05", "image/heic") for n in range(1500)])
    ScanRepo(conn).upsert_drive_files(rows)
    assert conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0] == 1500


def test_upsert_drive_files_revives_a_trashed_row(conn):
    """A file restored from Drive's trash must reappear in the library."""
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    conn.execute("UPDATE drive_files SET trashed_at = 'then' WHERE drive_id = 'd1'")
    conn.commit()

    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert conn.execute("SELECT trashed_at FROM drive_files").fetchone()["trashed_at"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scan_repo.py -v`
Expected: FAIL with `AttributeError: 'ScanRepo' object has no attribute 'upsert_drive_files'`.

- [ ] **Step 3: Replace `replace_drive_files`**

In `photolib/db/scan_repo.py`, replace the whole `replace_drive_files` method with:

```python
    def upsert_drive_files(self, rows: list[dict]) -> None:
        """Refresh the destination index without disturbing tags.

        Tags key on `drive_id`, so deleting and re-inserting this table — as
        an earlier version did — silently threw every tag away. Upserting
        keeps the row; the sweep afterwards drops only what Drive no longer
        has. The sweep compares `indexed_at` rather than listing ids, because
        1,284 ids exceed SQLite's host-parameter limit.
        """
        stamp = _now()
        self._conn.executemany(
            "INSERT INTO drive_files "
            "  (drive_id, name, parent_path, md5, size, mime_type, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(drive_id) DO UPDATE SET "
            "  name = excluded.name, parent_path = excluded.parent_path, "
            "  md5 = excluded.md5, size = excluded.size, "
            "  mime_type = excluded.mime_type, indexed_at = excluded.indexed_at, "
            "  trashed_at = NULL",
            [
                (
                    r["drive_id"], r["name"], r["parent_path"], r["md5"],
                    r["size"], r.get("mime_type"), stamp,
                )
                for r in rows
            ],
        )
        self._conn.execute(
            "DELETE FROM drive_files WHERE indexed_at IS NOT ?", (stamp,)
        )
        self._conn.commit()
```

- [ ] **Step 4: Record the mime type when scanning**

In `photolib/actions/scan_archives.py`, `_index_destination` builds those rows. Add `mime_type` to both branches and call the new method:

```python
def _index_destination(ctx: ActionContext, folder_id: str) -> int:
    """Walk the destination two levels deep and return how many files were seen."""
    rows: list[dict] = []
    for child in ctx.drive.list_children(folder_id):
        if not child.is_folder:
            rows.append(
                {
                    "drive_id": child.id, "name": child.name, "parent_path": "",
                    "md5": child.md5, "size": child.size,
                    "mime_type": child.mime_type,
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
                    "size": grandchild.size, "mime_type": grandchild.mime_type,
                }
            )
    ScanRepo(ctx.conn).upsert_drive_files(rows)
    return len(rows)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scan_repo.py tests/test_action_scan.py -v`
Expected: PASS.

- [ ] **Step 6: Confirm nothing else called the old method**

Run: `rg "replace_drive_files" --type py`
Expected: no matches. If any remain, update them to `upsert_drive_files` and re-run the suite.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add photolib/db/scan_repo.py photolib/actions/scan_archives.py tests/test_scan_repo.py
git commit -m "fix(scan): upsert the destination index so a re-scan cannot delete tags"
```

---

## Task 4: LibraryRepo — the filtered query and its facets

**Files:**
- Create: `photolib/db/library_repo.py`
- Test: `tests/test_library_repo.py`

**Interfaces:**
- Consumes: `drive_files` (Task 3), `media.drive_file_id` (written by Organize).
- Produces:

```python
@dataclass(frozen=True)
class Filters:
    month: str | None = None
    place: str | None = None
    country: str | None = None
    media_type: str | None = None      # 'image' | 'video' | 'other'
    tag_id: int | None = None
    duplicates: bool = False
    search: str | None = None

class LibraryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None: ...
    def list_files(self, filters: Filters, limit: int, offset: int) -> dict: ...
    #   -> {"total": int, "rows": [ ...ROW_FIELDS... ]}
    def all_ids(self, filters: Filters) -> list[str]: ...
    def facets(self) -> dict: ...
    def detail(self, drive_id: str) -> dict | None: ...
```

Row fields: `drive_id, name, month, mime_type, media_type, size, md5, capture_time, place, country, duplicate_of, duplicate_reason, archive_name, capture_source`.

The join is `drive_files LEFT JOIN media ON media.drive_file_id = drive_files.drive_id` — exact, because `organize.py` records `drive_file_id` on success. A file uploaded by other means has no `media` row and shows up with nulls rather than vanishing. `month` is `parent_path`, which Organize sets to `YYYY-MM`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_library_repo.py`:

```python
import pytest

from photolib.db.library_repo import Filters, LibraryRepo


@pytest.fixture
def library(conn):
    """Two months, a video, a duplicate, and one file this tool never uploaded."""
    conn.execute("INSERT INTO archives (drive_id, name, size) VALUES ('a1', 'part-001.zip', 99)")
    files = [
        # drive_id, name, month, mime
        ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
        ("d2", "IMG_2.HEIC", "2025-05", "image/heic"),
        ("d3", "VID_1.MOV", "2025-06", "video/quicktime"),
        ("d4", "NOTES.txt", "2025-06", "text/plain"),
        ("orphan", "STRAY.JPG", "2025-06", "image/jpeg"),
    ]
    for drive_id, name, month, mime in files:
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
            "VALUES (?, ?, ?, 'md5', 100, ?)",
            (drive_id, name, month, mime),
        )
    # media rows for everything but 'orphan'
    rows = [
        # entry path, drive_file_id, capture, place, country, dup_of, dup_reason
        ("Takeout/1.HEIC", "d1", 1700000000, "Warsaw", "Poland", None, None),
        ("Takeout/2.HEIC", "d2", 1700000100, "Lisbon", "Portugal", "2025-05",
         "name and size match an existing file"),
        ("Takeout/3.MOV", "d3", 1710000000, "Warsaw", "Poland", None, None),
        ("Takeout/4.txt", "d4", None, None, None, None, None),
    ]
    for index, (path, drive_id, capture, place, country, dup, reason) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO entries (id, archive_id, path, name, crc32, size, "
            "  compressed_size, method, local_header_offset, kind) "
            "VALUES (?, 1, ?, ?, 0, 100, 50, 8, 0, 'media')",
            (index, path, path.rsplit("/", 1)[-1]),
        )
        conn.execute(
            "INSERT INTO media (entry_id, capture_time, capture_source, place, "
            "  country, duplicate_of, duplicate_reason, upload_status, drive_file_id) "
            "VALUES (?, ?, 'sidecar', ?, ?, ?, ?, 'done', ?)",
            (index, capture, place, country, dup, reason, drive_id),
        )
    conn.commit()
    return LibraryRepo(conn)


def test_lists_everything_by_default(library):
    result = library.list_files(Filters(), limit=100, offset=0)
    assert result["total"] == 5
    assert {row["drive_id"] for row in result["rows"]} == {"d1", "d2", "d3", "d4", "orphan"}


def test_a_file_with_no_media_row_still_appears(library):
    """Anything dropped into Photos/ by other means must be browsable."""
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    orphan = next(row for row in rows if row["drive_id"] == "orphan")
    assert orphan["name"] == "STRAY.JPG"
    assert orphan["capture_time"] is None
    assert orphan["place"] is None
    assert orphan["month"] == "2025-06"


def test_rows_carry_the_source_archive(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    first = next(row for row in rows if row["drive_id"] == "d1")
    assert first["archive_name"] == "part-001.zip"


def test_filter_by_month(library):
    result = library.list_files(Filters(month="2025-05"), limit=100, offset=0)
    assert result["total"] == 2


def test_filter_by_place(library):
    result = library.list_files(Filters(place="Warsaw"), limit=100, offset=0)
    assert {row["drive_id"] for row in result["rows"]} == {"d1", "d3"}


def test_filter_by_country(library):
    result = library.list_files(Filters(country="Portugal"), limit=100, offset=0)
    assert result["total"] == 1


def test_filter_by_media_type(library):
    assert library.list_files(Filters(media_type="image"), 100, 0)["total"] == 3
    assert library.list_files(Filters(media_type="video"), 100, 0)["total"] == 1
    assert library.list_files(Filters(media_type="other"), 100, 0)["total"] == 1


def test_media_type_is_derived_on_every_row(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    by_id = {row["drive_id"]: row["media_type"] for row in rows}
    assert by_id == {
        "d1": "image", "d2": "image", "d3": "video",
        "d4": "other", "orphan": "image",
    }


def test_filter_by_duplicates(library):
    result = library.list_files(Filters(duplicates=True), limit=100, offset=0)
    assert result["total"] == 1
    assert result["rows"][0]["duplicate_reason"] == "name and size match an existing file"


def test_filter_by_search_is_case_insensitive(library):
    assert library.list_files(Filters(search="vid_"), 100, 0)["total"] == 1


def test_filter_by_tag(conn, library):
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d3', 1)")
    conn.commit()

    result = library.list_files(Filters(tag_id=1), limit=100, offset=0)
    assert [row["drive_id"] for row in result["rows"]] == ["d3"]


def test_filters_compose(library):
    result = library.list_files(
        Filters(month="2025-06", media_type="video"), limit=100, offset=0
    )
    assert [row["drive_id"] for row in result["rows"]] == ["d3"]


def test_trashed_files_are_never_listed(conn, library):
    conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    conn.commit()
    assert library.list_files(Filters(), 100, 0)["total"] == 4


def test_total_ignores_the_page_window(library):
    result = library.list_files(Filters(), limit=2, offset=0)
    assert result["total"] == 5
    assert len(result["rows"]) == 2


def test_offset_pages_without_repeating(library):
    first = library.list_files(Filters(), limit=2, offset=0)["rows"]
    second = library.list_files(Filters(), limit=2, offset=2)["rows"]
    assert not ({r["drive_id"] for r in first} & {r["drive_id"] for r in second})


def test_ordering_is_newest_month_first_then_name(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    assert [row["drive_id"] for row in rows] == ["d4", "orphan", "d3", "d1", "d2"]


def test_all_ids_honours_the_filter_and_ignores_paging(library):
    """'Select all matching this filter' must reach past the rendered page."""
    assert library.all_ids(Filters(month="2025-05")) == ["d1", "d2"]


def test_facets_count_each_dimension(library):
    facets = library.facets()
    assert facets["total"] == 5
    assert facets["months"] == [
        {"value": "2025-06", "count": 3},
        {"value": "2025-05", "count": 2},
    ]
    assert {"value": "Warsaw", "count": 2} in facets["places"]
    assert {"value": "Poland", "count": 2} in facets["countries"]
    assert facets["types"] == [
        {"value": "image", "count": 3},
        {"value": "other", "count": 1},
        {"value": "video", "count": 1},
    ]
    assert facets["duplicates"] == 1


def test_facets_omit_files_with_no_place(library):
    places = [f["value"] for f in library.facets()["places"]]
    assert None not in places


def test_detail_returns_one_file(library):
    detail = library.detail("d1")
    assert detail["name"] == "IMG_1.HEIC"
    assert detail["place"] == "Warsaw"
    assert detail["archive_name"] == "part-001.zip"


def test_detail_of_an_unknown_file_is_none(library):
    assert library.detail("nope") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_library_repo.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'photolib.db.library_repo'`.

- [ ] **Step 3: Write the implementation**

Create `photolib/db/library_repo.py`:

```python
"""The query behind the Library page.

Browses `drive_files` — what Drive actually holds, as of the last Scan — and
left-joins the catalog for the things only the catalog knows: capture time,
place, source archive, duplicate verdict. The join is exact rather than
name-based, because Organize records `media.drive_file_id` on every success.
A file that arrived in the destination by some other route keeps its row and
shows nulls; it is never hidden.

Month, place, country, type and duplicate status are computed here rather than
stored as tags. That is the whole reason Phase 4 has no `retag` action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

ROW_FIELDS = (
    "drive_id", "name", "month", "mime_type", "media_type", "size", "md5",
    "capture_time", "capture_source", "place", "country",
    "duplicate_of", "duplicate_reason", "archive_name",
)

# `media_type` in SQL, so filtering and selecting agree by construction.
_MEDIA_TYPE = (
    "CASE "
    "  WHEN d.mime_type LIKE 'image/%' THEN 'image' "
    "  WHEN d.mime_type LIKE 'video/%' THEN 'video' "
    "  ELSE 'other' "
    "END"
)

_FROM = (
    "FROM drive_files d "
    "LEFT JOIN media m ON m.drive_file_id = d.drive_id "
    "LEFT JOIN entries e ON e.id = m.entry_id "
    "LEFT JOIN archives a ON a.id = e.archive_id "
)

_SELECT = (
    "SELECT d.drive_id, d.name, d.parent_path AS month, d.mime_type, "
    f"       {_MEDIA_TYPE} AS media_type, d.size, d.md5, "
    "       m.capture_time, m.capture_source, m.place, m.country, "
    "       m.duplicate_of, m.duplicate_reason, a.name AS archive_name "
)

# Newest month first — the way you actually look for a recent photo — then by
# name within the month, which keeps a Live Photo's HEIC and MOV adjacent.
_ORDER = "ORDER BY d.parent_path DESC, d.name ASC"


@dataclass(frozen=True)
class Filters:
    month: str | None = None
    place: str | None = None
    country: str | None = None
    media_type: str | None = None
    tag_id: int | None = None
    duplicates: bool = False
    search: str | None = None


def _where(filters: Filters) -> tuple[str, list]:
    clauses = ["d.trashed_at IS NULL"]
    args: list = []
    if filters.month:
        clauses.append("d.parent_path = ?")
        args.append(filters.month)
    if filters.place:
        clauses.append("m.place = ?")
        args.append(filters.place)
    if filters.country:
        clauses.append("m.country = ?")
        args.append(filters.country)
    if filters.media_type:
        clauses.append(f"{_MEDIA_TYPE} = ?")
        args.append(filters.media_type)
    if filters.duplicates:
        clauses.append("m.duplicate_of IS NOT NULL")
    if filters.search:
        clauses.append("d.name LIKE ? ESCAPE '\\'")
        escaped = (
            filters.search.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        args.append(f"%{escaped}%")
    if filters.tag_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM file_tags ft "
            "        WHERE ft.drive_id = d.drive_id AND ft.tag_id = ?)"
        )
        args.append(filters.tag_id)
    return "WHERE " + " AND ".join(clauses), args


class LibraryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_files(self, filters: Filters, limit: int, offset: int) -> dict:
        clause, args = _where(filters)
        total = self._conn.execute(
            f"SELECT COUNT(*) {_FROM} {clause}", args
        ).fetchone()[0]
        rows = self._conn.execute(
            f"{_SELECT} {_FROM} {clause} {_ORDER} LIMIT ? OFFSET ?",
            [*args, limit, offset],
        )
        return {
            "total": total,
            "rows": [{field: row[field] for field in ROW_FIELDS} for row in rows],
        }

    def all_ids(self, filters: Filters) -> list[str]:
        """Every id matching the filter — what 'select all matching' selects."""
        clause, args = _where(filters)
        return [
            row["drive_id"]
            for row in self._conn.execute(
                f"SELECT d.drive_id {_FROM} {clause} {_ORDER}", args
            )
        ]

    def detail(self, drive_id: str) -> dict | None:
        row = self._conn.execute(
            f"{_SELECT} {_FROM} WHERE d.drive_id = ?", (drive_id,)
        ).fetchone()
        return None if row is None else {f: row[f] for f in ROW_FIELDS}

    def facets(self) -> dict:
        def group(expression: str, order: str) -> list[dict]:
            return [
                {"value": row["value"], "count": row["count"]}
                for row in self._conn.execute(
                    f"SELECT {expression} AS value, COUNT(*) AS count {_FROM} "
                    f"WHERE d.trashed_at IS NULL AND {expression} IS NOT NULL "
                    f"GROUP BY value ORDER BY {order}"
                )
            ]

        def count(clause: str) -> int:
            return self._conn.execute(
                f"SELECT COUNT(*) {_FROM} WHERE d.trashed_at IS NULL AND {clause}"
            ).fetchone()[0]

        return {
            "total": count("1 = 1"),
            "months": group("d.parent_path", "value DESC"),
            "places": group("m.place", "count DESC, value ASC"),
            "countries": group("m.country", "count DESC, value ASC"),
            "types": group(_MEDIA_TYPE, "count DESC, value ASC"),
            "duplicates": count("m.duplicate_of IS NOT NULL"),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_library_repo.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Commit**

```bash
git add photolib/db/library_repo.py tests/test_library_repo.py
git commit -m "feat(db): LibraryRepo — filtered browse over drive_files with derived facets"
```

---

## Task 5: Drive thumbnail and appProperties access

**Files:**
- Modify: `photolib/drive/client.py:16,19-32`
- Modify: `photolib/drive/writer.py:85-96`
- Modify: `tests/fakes/fake_drive.py`
- Test: `tests/test_drive_client.py`, `tests/test_drive_writer.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:

```python
# DriveFile
thumbnail_link: str | None   # alias 'thumbnailLink'

# DriveClient
def fetch_thumbnail(self, file_id: str, size: int) -> bytes | None: ...
def app_properties(self, file_id: str) -> dict[str, str]: ...

# DriveWriter
def update_properties(self, file_id: str, properties: dict[str, str | None]) -> None: ...
```

`fetch_thumbnail` returns `None` when Drive has not generated a thumbnail yet — which happens for a few minutes after upload, and permanently for formats it cannot render. That is a normal state, not an error, so it is not an exception. Drive's `thumbnailLink` ends in a size suffix like `=s220`; swapping it for `=s400` is how you ask for a bigger one.

In `update_properties`, a value of `None` is how the Drive API **deletes** a property. That is the only reason the signature takes `str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drive_client.py`:

```python
def test_file_fields_request_the_thumbnail_link():
    from photolib.drive.client import FILE_FIELDS
    assert "thumbnailLink" in FILE_FIELDS


def test_fetch_thumbnail_rewrites_the_size_suffix():
    """Drive hands back =s220; the grid wants =s400 and the lightbox =s1600."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/f1"):
            return httpx.Response(
                200,
                json={
                    "id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic",
                    "thumbnailLink": "https://lh3.example/abc=s220",
                },
            )
        requested.append(str(request.url))
        return httpx.Response(200, content=b"jpegbytes")

    client = DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.fetch_thumbnail("f1", 400) == b"jpegbytes"
    assert requested == ["https://lh3.example/abc=s400"]


def test_fetch_thumbnail_appends_a_size_when_the_link_has_none():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/f1"):
            return httpx.Response(
                200,
                json={
                    "id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic",
                    "thumbnailLink": "https://lh3.example/abc",
                },
            )
        assert str(request.url) == "https://lh3.example/abc=s400"
        return httpx.Response(200, content=b"jpegbytes")

    client = DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.fetch_thumbnail("f1", 400) == b"jpegbytes"


def test_fetch_thumbnail_is_none_when_drive_has_not_made_one():
    """Freshly uploaded files have no thumbnailLink for a few minutes."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic"}
        )

    client = DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.fetch_thumbnail("f1", 400) is None


def test_app_properties_returns_an_empty_dict_when_there_are_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.app_properties("f1") == {}


def test_app_properties_returns_what_drive_holds():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fields"] == "appProperties"
        return httpx.Response(200, json={"appProperties": {"t_family": "1"}})

    client = DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.app_properties("f1") == {"t_family": "1"}
```

These tests reuse `_tokens()` and the `httpx.MockTransport` pattern the existing tests in this file already use. If the helper is named something else there, use that name — do not introduce a second way of building a client. The same applies to `tests/test_drive_writer.py` below, which additionally needs `json` imported to read back the patched body.

Append to `tests/test_drive_writer.py`:

```python
def test_update_properties_patches_app_properties():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "f1"})

    writer = DriveWriter(
        DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    )
    writer.update_properties("f1", {"t_family": "1"})

    assert seen == [{"appProperties": {"t_family": "1"}}]


def test_update_properties_sends_null_to_delete_one():
    """Drive deletes an appProperty when its value is null, not by omission."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "f1"})

    writer = DriveWriter(
        DriveClient(_tokens(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    )
    writer.update_properties("f1", {"t_gone": None})

    assert seen == [{"appProperties": {"t_gone": None}}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_drive_client.py tests/test_drive_writer.py -v`
Expected: FAIL — `assert 'thumbnailLink' in FILE_FIELDS`, and `AttributeError: 'DriveClient' object has no attribute 'fetch_thumbnail'`.

- [ ] **Step 3: Extend `DriveClient`**

In `photolib/drive/client.py`, add `re` to the imports, extend `FILE_FIELDS`, add the field to `DriveFile`, and append two methods:

```python
FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,modifiedTime,parents,thumbnailLink"
)

# Drive's thumbnailLink ends in a size directive: .../abc=s220. Swapping it is
# how you ask for a different size; there is no query parameter for it.
_SIZE_SUFFIX = re.compile(r"=s\d+(-c)?$")
```

On `DriveFile`:

```python
    thumbnail_link: str | None = Field(default=None, alias="thumbnailLink")
```

And on `DriveClient`:

```python
    def fetch_thumbnail(self, file_id: str, size: int) -> bytes | None:
        """Drive's own render of a file, or None if it has not made one.

        Chrome cannot display HEIC, and 591 of these files are HEIC, so this
        is how the Library shows anything at all. A missing thumbnailLink is
        ordinary — Drive generates them asynchronously after upload — and is
        reported as None rather than raised.
        """
        link = self.get_file(file_id).thumbnail_link
        if not link:
            return None
        if _SIZE_SUFFIX.search(link):
            link = _SIZE_SUFFIX.sub(f"=s{size}", link)
        else:
            link = f"{link}=s{size}"
        return self._fetch_thumbnail_bytes(link)

    @retry
    def _fetch_thumbnail_bytes(self, link: str) -> bytes:
        response = self._http.get(link, headers=self.headers())
        raise_for_response(response)
        return response.content

    @retry
    def app_properties(self, file_id: str) -> dict[str, str]:
        """The file's private appProperties. `sync_tags` diffs against these."""
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"fields": "appProperties", "supportsAllDrives": "true"},
            headers=self.headers(),
        )
        raise_for_response(response)
        return response.json().get("appProperties") or {}
```

- [ ] **Step 4: Extend `DriveWriter`**

In `photolib/drive/writer.py`, add after `trash`:

```python
    # ---------- properties ----------

    @retry
    def update_properties(
        self, file_id: str, properties: dict[str, str | None]
    ) -> None:
        """Set or clear private appProperties on a file.

        A value of None deletes that property — the API's own convention, and
        the only way `sync_tags` can remove a tag it previously wrote.
        """
        response = self._http.patch(
            f"{API_ROOT}/files/{file_id}",
            params={"supportsAllDrives": "true", "fields": "id"},
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps({"appProperties": properties}),
        )
        raise_for_response(response)
```

- [ ] **Step 5: Teach the fake the same two things**

In `tests/fakes/fake_drive.py`, add a thumbnail store and the two methods. In `__init__`:

```python
        self._thumbnails: dict[str, bytes] = {}
        self.thumbnail_requests: list[tuple[str, int]] = []
```

Then a helper and the interface methods:

```python
    def set_thumbnail(self, file_id: str, content: bytes) -> None:
        """Test helper: pretend Drive has rendered a thumbnail for this file."""
        self._thumbnails[file_id] = content

    # --- DriveClient interface ---

    def fetch_thumbnail(self, file_id: str, size: int) -> bytes | None:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        self.thumbnail_requests.append((file_id, size))
        content = self._thumbnails.get(file_id)
        return None if content is None else content + f"-s{size}".encode()

    def app_properties(self, file_id: str) -> dict[str, str]:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        return dict(self._properties.get(file_id, {}))

    # --- DriveWriter interface ---

    def update_properties(
        self, file_id: str, properties: dict[str, str | None]
    ) -> None:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        current = self._properties.setdefault(file_id, {})
        for key, value in properties.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
```

The size is folded into the returned bytes so a test can prove the grid asked for `s400` and the lightbox for `s1600`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_drive_client.py tests/test_drive_writer.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. `FILE_FIELDS` changed, so watch `tests/test_action_scan.py` and `tests/test_api_drive.py` — if either asserts on the exact field string, update it to the new value rather than reverting the change.

- [ ] **Step 8: Commit**

```bash
git add photolib/drive/client.py photolib/drive/writer.py tests/fakes/fake_drive.py \
        tests/test_drive_client.py tests/test_drive_writer.py
git commit -m "feat(drive): fetch thumbnails at a chosen size and read/write appProperties"
```

---

## Task 6: The thumbnail disk cache

**Files:**
- Create: `photolib/thumbs.py`
- Test: `tests/test_thumbs.py`

**Interfaces:**
- Consumes: `DriveClient.fetch_thumbnail(file_id, size) -> bytes | None` (Task 5), `Config.thumbnail_cache_dir` (already exists at `config.py:16`).
- Produces:

```python
SIZES: tuple[int, ...] = (400, 1600)

class ThumbnailUnavailable(Exception): ...

class ThumbnailCache:
    def __init__(self, root: Path, drive) -> None: ...
    def path_for(self, drive_id: str, size: int) -> Path: ...
    def get(self, drive_id: str, size: int) -> bytes: ...   # raises ThumbnailUnavailable
```

`Config` already carries `thumbnail_cache_dir` (`root / ".cache" / "thumbnails"`) and `.cache/` is already gitignored, so nothing new needs configuring. Two sizes only — 400 for the grid, 1600 for the lightbox — because an open-ended size parameter is an unbounded disk cache keyed by whatever a URL asks for.

Writes go to a temporary file and are then renamed. A half-written cache entry that a later request happily serves is the failure mode worth designing out; `os.replace` is atomic on the same filesystem.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_thumbs.py`:

```python
import pytest

from photolib.thumbs import SIZES, ThumbnailCache, ThumbnailUnavailable
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"heic-bytes", parent="root")
    fake.set_thumbnail("d1", b"jpeg")
    return fake


def test_sizes_are_the_two_the_ui_asks_for():
    assert SIZES == (400, 1600)


def test_a_miss_fetches_from_drive(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    assert cache.get("d1", 400) == b"jpeg-s400"
    assert drive.thumbnail_requests == [("d1", 400)]


def test_a_hit_does_not_touch_drive(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    drive.thumbnail_requests.clear()

    assert cache.get("d1", 400) == b"jpeg-s400"
    assert drive.thumbnail_requests == []


def test_each_size_is_cached_separately(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    assert cache.get("d1", 400) == b"jpeg-s400"
    assert cache.get("d1", 1600) == b"jpeg-s1600"


def test_the_bytes_land_on_disk(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    assert cache.path_for("d1", 400).read_bytes() == b"jpeg-s400"


def test_no_part_files_survive_a_successful_fetch(tmp_path, drive):
    """A half-written entry served as a whole one is the bug worth preventing."""
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    assert list(tmp_path.glob("**/*.part")) == []


def test_a_file_drive_has_not_rendered_is_unavailable(tmp_path, drive):
    drive.add_file("d2", "IMG_2.HEIC", b"x", parent="root")   # no set_thumbnail
    cache = ThumbnailCache(tmp_path, drive)

    with pytest.raises(ThumbnailUnavailable):
        cache.get("d2", 400)


def test_an_unavailable_thumbnail_is_not_cached_as_empty(tmp_path, drive):
    """Otherwise a file uploaded a minute ago would never get a thumbnail."""
    drive.add_file("d2", "IMG_2.HEIC", b"x", parent="root")
    cache = ThumbnailCache(tmp_path, drive)
    with pytest.raises(ThumbnailUnavailable):
        cache.get("d2", 400)

    drive.set_thumbnail("d2", b"late")
    assert cache.get("d2", 400) == b"late-s400"


def test_an_unknown_size_is_refused(tmp_path, drive):
    with pytest.raises(ValueError):
        ThumbnailCache(tmp_path, drive).get("d1", 9999)


def test_a_drive_id_cannot_escape_the_cache_directory(tmp_path, drive):
    """Drive ids reach this from a URL path; treat them as hostile."""
    cache = ThumbnailCache(tmp_path, drive)
    with pytest.raises(ValueError):
        cache.path_for("../../etc/passwd", 400)


def test_the_cache_directory_is_created_on_demand(tmp_path, drive):
    cache = ThumbnailCache(tmp_path / "does" / "not" / "exist", drive)
    assert cache.get("d1", 400) == b"jpeg-s400"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_thumbs.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'photolib.thumbs'`.

- [ ] **Step 3: Write the implementation**

Create `photolib/thumbs.py`:

```python
"""A disk cache in front of Drive's own thumbnail renderer.

Chrome cannot display HEIC and 591 of these files are HEIC, so the Library
cannot render the media directly. Drive already generates thumbnails for
everything it holds; this fetches them once and keeps the bytes, so scrolling
the grid a second time costs nothing.

Two sizes only. An open-ended size parameter arriving from a URL is an
unbounded cache keyed by whatever anyone asks for.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# 400 for the grid, 1600 for the lightbox.
SIZES: tuple[int, ...] = (400, 1600)

# Drive ids are URL-safe base64-ish. Anything else reaching here came from a
# crafted path, not from Drive.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ThumbnailUnavailable(Exception):
    """Drive has no thumbnail for this file — often just 'not yet'."""


class ThumbnailCache:
    def __init__(self, root: Path, drive) -> None:
        self._root = Path(root)
        self._drive = drive

    def path_for(self, drive_id: str, size: int) -> Path:
        if not _SAFE_ID.match(drive_id):
            raise ValueError(f"not a Drive file id: {drive_id!r}")
        if size not in SIZES:
            raise ValueError(f"size must be one of {SIZES}, not {size}")
        # Two levels of fanout: a flat directory of 1,284+ files is slow to
        # list and unpleasant to inspect by hand.
        return self._root / drive_id[:2] / f"{drive_id}-s{size}.jpg"

    def get(self, drive_id: str, size: int) -> bytes:
        """Cached bytes, fetching them from Drive on a miss."""
        path = self.path_for(drive_id, size)
        if path.exists():
            return path.read_bytes()

        content = self._drive.fetch_thumbnail(drive_id, size)
        if content is None:
            # Deliberately not cached. Drive generates thumbnails a little
            # after upload, so caching the absence would make a freshly
            # organised library permanently blank.
            raise ThumbnailUnavailable(
                f"Drive has not generated a thumbnail for {drive_id} yet"
            )

        self._write(path, content)
        return content

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        """Write atomically, so a truncated file is never served as whole."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, raw = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(content)
            os.replace(raw, path)
        except BaseException:
            Path(raw).unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_thumbs.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add photolib/thumbs.py tests/test_thumbs.py
git commit -m "feat(thumbs): disk-cached proxy for Drive's thumbnails, two sizes"
```

---

## Task 7: Library and thumbnail API routes

**Files:**
- Create: `photolib/api/routes_library.py`
- Create: `photolib/api/routes_thumbs.py`
- Modify: `photolib/api/app.py:64-86`
- Test: `tests/test_api_library.py`, `tests/test_api_thumbs.py`

**Interfaces:**
- Consumes: `LibraryRepo`, `Filters` (Task 4); `TagsRepo.tags_for` (Task 2); `ThumbnailCache` (Task 6).
- Produces these routes, all read-only:

| Route | Returns |
| --- | --- |
| `GET /api/library/files` | `{total, rows: [...+ tags]}` |
| `GET /api/library/ids` | `{ids: [...]}` |
| `GET /api/library/facets` | `{total, months, places, countries, types, duplicates}` |
| `GET /api/library/file/{drive_id}` | one row plus its tags |
| `GET /api/thumb/{drive_id}?size=400` | JPEG bytes, or **202** while Drive renders |

Also produces `app.state.thumbnails`.

Query parameters on `/files` and `/ids` are identical, so both build `Filters` through one helper. `limit` is capped at 500 — a page bigger than that is a client bug, and the grid pages by scrolling anyway.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_library.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path):
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    app = create_app(config=config, drive=FakeDrive())
    with TestClient(app) as test_client:
        conn = app.state.conn
        for drive_id, name, month, mime in [
            ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
            ("d2", "VID_1.MOV", "2025-06", "video/quicktime"),
        ]:
            conn.execute(
                "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
                "VALUES (?, ?, ?, 'md5', 100, ?)",
                (drive_id, name, month, mime),
            )
        conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
        conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d1', 1)")
        conn.commit()
        yield test_client


def test_files_lists_everything(client):
    body = client.get("/api/library/files").json()
    assert body["total"] == 2
    assert {row["drive_id"] for row in body["rows"]} == {"d1", "d2"}


def test_files_carry_their_tags(client):
    rows = {r["drive_id"]: r for r in client.get("/api/library/files").json()["rows"]}
    assert [t["slug"] for t in rows["d1"]["tags"]] == ["family"]
    assert rows["d2"]["tags"] == []


def test_files_filter_by_month(client):
    body = client.get("/api/library/files?month=2025-06").json()
    assert [row["drive_id"] for row in body["rows"]] == ["d2"]


def test_files_filter_by_type(client):
    body = client.get("/api/library/files?media_type=video").json()
    assert body["total"] == 1


def test_files_filter_by_tag(client):
    body = client.get("/api/library/files?tag_id=1").json()
    assert [row["drive_id"] for row in body["rows"]] == ["d1"]


def test_files_reject_an_oversized_page(client):
    assert client.get("/api/library/files?limit=5000").status_code == 422


def test_ids_returns_the_whole_filtered_set(client):
    assert client.get("/api/library/ids?month=2025-05").json() == {"ids": ["d1"]}


def test_facets_report_each_dimension(client):
    body = client.get("/api/library/facets").json()
    assert body["total"] == 2
    assert body["months"] == [
        {"value": "2025-06", "count": 1},
        {"value": "2025-05", "count": 1},
    ]
    assert body["duplicates"] == 0


def test_file_detail_includes_tags(client):
    body = client.get("/api/library/file/d1").json()
    assert body["name"] == "IMG_1.HEIC"
    assert [t["slug"] for t in body["tags"]] == ["family"]


def test_file_detail_404s_for_an_unknown_id(client):
    assert client.get("/api/library/file/nope").status_code == 404
```

Create `tests/test_api_thumbs.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"heic", parent="root")
    fake.set_thumbnail("d1", b"jpeg")
    fake.add_file("d2", "IMG_2.HEIC", b"heic", parent="root")   # not rendered yet
    return fake


@pytest.fixture
def client(tmp_path, drive):
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    with TestClient(create_app(config=config, drive=drive)) as test_client:
        yield test_client


def test_serves_jpeg_bytes(client):
    response = client.get("/api/thumb/d1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-s400"


def test_defaults_to_the_grid_size(client, drive):
    client.get("/api/thumb/d1")
    assert drive.thumbnail_requests == [("d1", 400)]


def test_the_lightbox_size_is_available(client):
    assert client.get("/api/thumb/d1?size=1600").content == b"jpeg-s1600"


def test_an_arbitrary_size_is_refused(client):
    assert client.get("/api/thumb/d1?size=64").status_code == 422


def test_a_second_request_is_served_from_disk(client, drive):
    client.get("/api/thumb/d1")
    drive.thumbnail_requests.clear()
    assert client.get("/api/thumb/d1").content == b"jpeg-s400"
    assert drive.thumbnail_requests == []


def test_a_file_drive_has_not_rendered_yet_returns_202(client):
    """202 tells the tile to show a placeholder and try again, not to break."""
    assert client.get("/api/thumb/d2").status_code == 202


def test_an_unknown_file_is_404(client):
    assert client.get("/api/thumb/nosuchfile").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_library.py tests/test_api_thumbs.py -v`
Expected: FAIL — every request 404s, because no such routers are mounted.

- [ ] **Step 3: Write the library routes**

Create `photolib/api/routes_library.py`:

```python
"""Read-only endpoints backing the Library page.

Nothing here writes. Tagging lives in `routes_tags`, and the only thing that
touches Drive is the `sync_tags` action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from photolib.db.library_repo import Filters, LibraryRepo
from photolib.db.tags_repo import TagsRepo

router = APIRouter(tags=["library"])


def _filters(
    month: str | None = None,
    place: str | None = None,
    country: str | None = None,
    media_type: str | None = Query(default=None, pattern="^(image|video|other)$"),
    tag_id: int | None = None,
    duplicates: bool = False,
    search: str | None = None,
) -> Filters:
    return Filters(
        month=month, place=place, country=country, media_type=media_type,
        tag_id=tag_id, duplicates=duplicates, search=search,
    )


@router.get("/library/files")
def files(
    request: Request,
    filters: Filters = Depends(_filters),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    conn = request.app.state.conn
    result = LibraryRepo(conn).list_files(filters, limit=limit, offset=offset)
    tags = TagsRepo(conn).tags_for([row["drive_id"] for row in result["rows"]])
    return {
        "total": result["total"],
        "rows": [
            {**row, "tags": tags.get(row["drive_id"], [])} for row in result["rows"]
        ],
    }


@router.get("/library/ids")
def ids(request: Request, filters: Filters = Depends(_filters)) -> dict:
    """Every id matching the filter — what 'select all matching' selects."""
    return {"ids": LibraryRepo(request.app.state.conn).all_ids(filters)}


@router.get("/library/facets")
def facets(request: Request) -> dict:
    return LibraryRepo(request.app.state.conn).facets()


@router.get("/library/file/{drive_id}")
def file_detail(request: Request, drive_id: str) -> dict:
    conn = request.app.state.conn
    row = LibraryRepo(conn).detail(drive_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such file in the library")
    return {**row, "tags": TagsRepo(conn).tags_for([drive_id]).get(drive_id, [])}
```

`_filters` is shared by `/files` and `/ids` through `Depends`, so the two can never drift apart on what a filter means.

- [ ] **Step 4: Write the thumbnail route**

Create `photolib/api/routes_thumbs.py`:

```python
"""The image proxy. Drive holds the renders; this hands them to the browser."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from photolib.drive.errors import NotFoundError
from photolib.thumbs import ThumbnailUnavailable

router = APIRouter(tags=["thumbs"])


@router.get("/thumb/{drive_id}")
def thumb(request: Request, drive_id: str, size: int = Query(default=400)) -> Response:
    cache = request.app.state.thumbnails
    try:
        content = cache.get(drive_id, size)
    except ValueError as exc:
        # An unknown size or a malformed id: the caller's fault, not Drive's.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThumbnailUnavailable:
        # Not an error. Drive renders asynchronously after upload; the tile
        # shows a placeholder and asks again later.
        return Response(status_code=202)

    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
```

- [ ] **Step 5: Wire both into the app**

In `photolib/api/app.py`, import the cache near the other imports:

```python
from photolib.thumbs import ThumbnailCache
```

Add to the `app.state` block:

```python
    app.state.thumbnails = ThumbnailCache(cfg.thumbnail_cache_dir, drive_client)
```

And extend the router imports and registrations:

```python
    from photolib.api import (
        routes_actions,
        routes_drive,
        routes_jobs,
        routes_library,
        routes_review,
        routes_settings,
        routes_thumbs,
    )

    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(routes_drive.router, prefix="/api")
    app.include_router(routes_actions.router, prefix="/api")
    app.include_router(routes_jobs.router, prefix="/api")
    app.include_router(routes_review.router, prefix="/api")
    app.include_router(routes_library.router, prefix="/api")
    app.include_router(routes_thumbs.router, prefix="/api")
```

(`routes_tags` joins this list in Task 8.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_library.py tests/test_api_thumbs.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add photolib/api/routes_library.py photolib/api/routes_thumbs.py photolib/api/app.py \
        tests/test_api_library.py tests/test_api_thumbs.py
git commit -m "feat(api): library browse, facets, and the thumbnail proxy"
```

---

## Task 8: Tag API routes

**Files:**
- Create: `photolib/api/routes_tags.py`
- Modify: `photolib/api/app.py` (one import, one `include_router`)
- Test: `tests/test_api_tags.py`

**Interfaces:**
- Consumes: `TagsRepo`, `DuplicateTagError` (Task 2).
- Produces:

| Route | Body | Returns |
| --- | --- | --- |
| `GET /api/tags` | — | `[{id, name, slug, color, file_count}]` |
| `POST /api/tags` | `{name, color?}` | 201, the tag |
| `PATCH /api/tags/{id}` | `{name?, color?}` | the tag |
| `DELETE /api/tags/{id}` | — | `{deleted: id}` |
| `POST /api/tags/merge` | `{source_id, target_id}` | `{moved, target}` |
| `POST /api/tags/{id}/files` | `{drive_ids}` | `{added}` |
| `POST /api/tags/{id}/files/remove` | `{drive_ids}` | `{removed}` |

Removal is a `POST` to `/files/remove` rather than a `DELETE` with a body: `fetch` and several proxies treat a body on `DELETE` as undefined behaviour, and a bulk untag of 1,284 ids will not fit in a query string.

Everything here writes to SQLite only. Drive learns about tags when you run `sync_tags` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_tags.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path):
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    app = create_app(config=config, drive=FakeDrive())
    with TestClient(app) as test_client:
        conn = app.state.conn
        for drive_id in ("d1", "d2"):
            conn.execute(
                "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
                "VALUES (?, 'IMG.HEIC', '2025-05', 'md5', 100, 'image/heic')",
                (drive_id,),
            )
        conn.commit()
        yield test_client


def test_creating_a_tag_returns_201_and_the_row(client):
    response = client.post("/api/tags", json={"name": "Family"})
    assert response.status_code == 201
    assert response.json()["slug"] == "family"
    assert response.json()["file_count"] == 0


def test_creating_a_duplicate_is_409(client):
    client.post("/api/tags", json={"name": "Family"})
    assert client.post("/api/tags", json={"name": "family"}).status_code == 409


def test_creating_a_nameless_tag_is_422(client):
    assert client.post("/api/tags", json={"name": "   "}).status_code == 422


def test_listing_reports_counts(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    body = client.get("/api/tags").json()
    assert body == [
        {"id": tag["id"], "name": "Family", "slug": "family",
         "color": "#6b7280", "file_count": 2}
    ]


def test_adding_files_reports_how_many_were_new(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    first = client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1"]})
    second = client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    assert first.json() == {"added": 1}
    assert second.json() == {"added": 1}


def test_removing_files(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    response = client.post(
        f"/api/tags/{tag['id']}/files/remove", json={"drive_ids": ["d1"]}
    )
    assert response.json() == {"removed": 1}
    assert client.get("/api/tags").json()[0]["file_count"] == 1


def test_tagging_an_unknown_tag_is_404(client):
    assert client.post("/api/tags/999/files", json={"drive_ids": ["d1"]}).status_code == 404


def test_renaming(client):
    tag = client.post("/api/tags", json={"name": "Familly"}).json()
    body = client.patch(f"/api/tags/{tag['id']}", json={"name": "Family"}).json()
    assert (body["name"], body["slug"]) == ("Family", "family")


def test_renaming_onto_an_existing_name_is_409(client):
    client.post("/api/tags", json={"name": "Family"})
    other = client.post("/api/tags", json={"name": "Friends"}).json()
    assert client.patch(f"/api/tags/{other['id']}", json={"name": "Family"}).status_code == 409


def test_recolouring(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    body = client.patch(f"/api/tags/{tag['id']}", json={"color": "#ff0000"}).json()
    assert body["color"] == "#ff0000"


def test_patching_nothing_is_422(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    assert client.patch(f"/api/tags/{tag['id']}", json={}).status_code == 422


def test_deleting_a_tag_removes_its_assignments(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1"]})

    assert client.delete(f"/api/tags/{tag['id']}").status_code == 200
    assert client.get("/api/tags").json() == []
    assert client.get("/api/library/files?tag_id=%d" % tag["id"]).json()["total"] == 0


def test_merging(client):
    source = client.post("/api/tags", json={"name": "Familly"}).json()
    target = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{source['id']}/files", json={"drive_ids": ["d1", "d2"]})
    client.post(f"/api/tags/{target['id']}/files", json={"drive_ids": ["d2"]})

    body = client.post(
        "/api/tags/merge", json={"source_id": source["id"], "target_id": target["id"]}
    ).json()

    assert body["moved"] == 1
    assert [t["slug"] for t in client.get("/api/tags").json()] == ["family"]
    assert client.get("/api/tags").json()[0]["file_count"] == 2


def test_merging_a_tag_into_itself_is_422(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    response = client.post(
        "/api/tags/merge", json={"source_id": tag["id"], "target_id": tag["id"]}
    )
    assert response.status_code == 422


def test_merging_an_unknown_tag_is_404(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    response = client.post(
        "/api/tags/merge", json={"source_id": 999, "target_id": tag["id"]}
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api_tags.py -v`
Expected: FAIL — every request 404s, no router mounted.

- [ ] **Step 3: Write the routes**

Create `photolib/api/routes_tags.py`:

```python
"""Tag CRUD and bulk assignment. SQLite only — nothing here touches Drive.

Drive learns about tags when you run the `sync_tags` action, which reports
what it would change before it changes anything. Keeping the two apart is
what makes tagging in the UI instant and free of failure modes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from photolib.db.tags_repo import DEFAULT_COLOR, DuplicateTagError, TagsRepo

router = APIRouter(tags=["tags"])


class TagCreate(BaseModel):
    name: str
    color: str = DEFAULT_COLOR


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None


class FileList(BaseModel):
    drive_ids: list[str] = Field(default_factory=list)


class MergeRequest(BaseModel):
    source_id: int
    target_id: int


def _repo(request: Request) -> TagsRepo:
    return TagsRepo(request.app.state.conn)


def _row(repo: TagsRepo, tag_id: int) -> dict:
    """One tag with its count, so create and patch answer in the list's shape."""
    for row in repo.list_with_counts():
        if row["id"] == tag_id:
            return dict(row)
    raise HTTPException(status_code=404, detail="no such tag")


def _require(repo: TagsRepo, tag_id: int) -> None:
    if repo.get(tag_id) is None:
        raise HTTPException(status_code=404, detail="no such tag")


@router.get("/tags")
def list_tags(request: Request) -> list[dict]:
    return [dict(row) for row in _repo(request).list_with_counts()]


@router.post("/tags", status_code=201)
def create_tag(request: Request, body: TagCreate) -> dict:
    repo = _repo(request)
    try:
        tag = repo.create(body.name, body.color)
    except DuplicateTagError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _row(repo, tag["id"])


@router.patch("/tags/{tag_id}")
def patch_tag(request: Request, tag_id: int, body: TagPatch) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    if body.name is None and body.color is None:
        raise HTTPException(status_code=422, detail="give a name, a color, or both")
    try:
        if body.name is not None:
            repo.rename(tag_id, body.name)
        if body.color is not None:
            repo.recolor(tag_id, body.color)
    except DuplicateTagError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _row(repo, tag_id)


@router.delete("/tags/{tag_id}")
def delete_tag(request: Request, tag_id: int) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    repo.delete(tag_id)
    return {"deleted": tag_id}


@router.post("/tags/merge")
def merge_tags(request: Request, body: MergeRequest) -> dict:
    repo = _repo(request)
    _require(repo, body.source_id)
    _require(repo, body.target_id)
    try:
        moved = repo.merge(body.source_id, body.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"moved": moved, "target": _row(repo, body.target_id)}


@router.post("/tags/{tag_id}/files")
def add_files(request: Request, tag_id: int, body: FileList) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    return {"added": repo.add_files(tag_id, body.drive_ids)}


@router.post("/tags/{tag_id}/files/remove")
def remove_files(request: Request, tag_id: int, body: FileList) -> dict:
    """A POST, not a DELETE with a body — 1,284 ids do not fit in a URL."""
    repo = _repo(request)
    _require(repo, tag_id)
    return {"removed": repo.remove_files(tag_id, body.drive_ids)}
```

Note the route order: `/tags/merge` is declared before `/tags/{tag_id}/files`, but after `/tags/{tag_id}`. FastAPI matches in declaration order, and `merge` is not an integer, so `PATCH /tags/{tag_id}` cannot swallow it. Do not reorder these.

- [ ] **Step 4: Mount the router**

In `photolib/api/app.py`, add `routes_tags` to the import tuple and register it after the library router:

```python
    app.include_router(routes_tags.router, prefix="/api")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api_tags.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add photolib/api/routes_tags.py photolib/api/app.py tests/test_api_tags.py
git commit -m "feat(api): tag CRUD, merge, and bulk assignment"
```

---

## Task 9: The `sync_tags` action

**Files:**
- Create: `photolib/actions/sync_tags.py`
- Test: `tests/test_action_sync_tags.py`

**Interfaces:**
- Consumes: `TagsRepo.slugs_by_file()` (Task 2); `drive.app_properties(file_id)` (Task 5); `writer.update_properties(file_id, props)` (Task 5); `drive_files.synced_tags` (Task 1).
- Produces: an action registered as `sync_tags` with `Params(confirm: bool = False, limit: int = 0)`. The registry discovers it; no frontend work is needed for its page to appear.

**Why `synced_tags` exists.** The candidate set is every file that has catalog tags *or* has a non-empty `synced_tags`. Without the second half, untagging a file in the UI would drop it out of the candidate set entirely and its `t_*` property would sit on Drive forever — precisely the "additive only" drift this design rejected. Drive is still read live for the actual diff, so an externally edited file is handled correctly; `synced_tags` only widens who gets looked at.

**Property budget.** Organize already writes about five appProperties. Drive allows 30 per file, so a file carrying more than 25 tags is warned about and skipped rather than left to fail with an opaque API error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_action_sync_tags.py`:

```python
import pytest

from photolib.actions import sync_tags
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.settings_repo import SettingsRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"a", parent="root")
    fake.add_file("d2", "IMG_2.HEIC", b"b", parent="root")
    return fake


@pytest.fixture
def ctx(conn, drive, tmp_path):
    for drive_id in ("d1", "d2"):
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
            "VALUES (?, 'IMG.HEIC', '2025-05', 'md5', 1, 'image/heic')",
            (drive_id,),
        )
    conn.commit()
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    return ActionContext(
        conn=conn, drive=drive, settings=SettingsRepo(conn), config=config,
        writer=drive,
    )


def _tag(conn, name: str, slug: str, drive_ids: list[str]) -> None:
    cursor = conn.execute(
        "INSERT INTO tags (name, slug, color) VALUES (?, ?, '#f00')", (name, slug)
    )
    for drive_id in drive_ids:
        conn.execute(
            "INSERT INTO file_tags (drive_id, tag_id) VALUES (?, ?)",
            (drive_id, cursor.lastrowid),
        )
    conn.commit()


def _run(ctx, **params) -> list:
    return list(sync_tags.run(ctx, sync_tags.Params(**params)))


def test_declares_itself_to_the_registry():
    assert sync_tags.ID == "sync_tags"
    assert isinstance(sync_tags.ORDER, int)


def test_a_dry_run_changes_nothing(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    messages = " ".join(event.message for event in _run(ctx))

    assert drive.app_properties("d1") == {}
    assert "confirm" in messages.lower()


def test_a_dry_run_names_what_it_would_add(ctx):
    _tag(ctx.conn, "Family", "family", ["d1"])
    messages = [event.message for event in _run(ctx)]
    assert any("t_family" in message and "d1" in message for message in messages)


def test_confirm_writes_the_property(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    assert drive.app_properties("d1") == {"t_family": "1"}


def test_confirm_records_what_it_wrote(ctx):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    row = ctx.conn.execute(
        "SELECT synced_tags FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert row["synced_tags"] == "family"


def test_untagging_removes_the_property_on_the_next_sync(ctx, drive):
    """The drift this design exists to prevent."""
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    ctx.conn.execute("DELETE FROM file_tags")
    ctx.conn.commit()
    _run(ctx, confirm=True)

    assert drive.app_properties("d1") == {}


def test_a_file_that_was_never_tagged_is_not_visited(ctx, drive):
    """Visiting all 1,284 files would cost 1,284 API calls for nothing."""
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    assert drive.app_properties("d2") == {}


def test_properties_organize_wrote_are_left_alone(ctx, drive):
    """Only t_* belongs to sync_tags. capture_time and place are not its business."""
    drive.update_properties("d1", {"place": "Warsaw", "source_crc": "abc"})
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    assert drive.app_properties("d1") == {
        "place": "Warsaw", "source_crc": "abc", "t_family": "1"
    }


def test_a_file_already_in_sync_is_not_written_again(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    messages = " ".join(event.message for event in _run(ctx, confirm=True))

    assert "0 file(s) to change" in messages
    assert drive.app_properties("d1") == {"t_family": "1"}


def test_trashed_files_are_skipped(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    ctx.conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    ctx.conn.commit()
    _run(ctx, confirm=True)
    assert drive.app_properties("d1") == {}


def test_too_many_tags_is_refused_not_attempted(ctx, drive):
    """Drive caps appProperties at 30; Organize already used about five."""
    for index in range(26):
        _tag(ctx.conn, f"Tag {index}", f"tag-{index}", ["d1"])

    events = _run(ctx, confirm=True)

    assert any(event.level == "warn" for event in events)
    assert drive.app_properties("d1") == {}


def test_limit_caps_the_batch(ctx):
    _tag(ctx.conn, "Family", "family", ["d1", "d2"])
    messages = " ".join(event.message for event in _run(ctx, limit=1))
    assert "1 file(s)" in messages


def test_a_missing_writer_is_reported_not_crashed(ctx):
    ctx.writer = None
    events = _run(ctx)
    assert events[-1].level == "error"


def test_a_drive_failure_on_one_file_does_not_stop_the_run(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1", "d2"])
    drive.trash("d1")          # d1 vanishes; its update will raise NotFoundError

    events = _run(ctx, confirm=True)

    assert any(event.level == "error" for event in events)
    assert drive.app_properties("d2") == {"t_family": "1"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_action_sync_tags.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'sync_tags'`.

- [ ] **Step 3: Write the action**

Create `photolib/actions/sync_tags.py`:

```python
"""Mirror the catalog's tags onto Drive's appProperties.

The catalog is the query engine — unlimited tags, instant filtering, no API
calls. Drive is the durable copy: one property per tag (`t_family` = `1`), so
tags survive the loss of this machine, travel with the file, and stay
queryable by anything built later.

Shaped like `clear_stale_trees`, and for the same reason: it reports what it
would change and does nothing until you confirm.

The candidate set is every live file that has tags now, or had them last time
this ran. Without the second half, untagging a file would drop it out of the
set and leave its property on Drive forever. Drive itself is still read for
the diff, so a file edited elsewhere is reconciled correctly.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.tags_repo import TagsRepo
from photolib.drive.errors import DriveError

ID = "sync_tags"
TITLE = "Sync Tags to Drive"
DESCRIPTION = (
    "Make each file's Drive appProperties match its tags in the catalog, "
    "adding what is missing and removing what you untagged. Reports what it "
    "would do unless you confirm."
)
ORDER = 60

PREFIX = "t_"

# Drive allows 30 appProperties per file and Organize already writes about
# five. Refusing at 25 turns an opaque API failure into a clear warning.
MAX_TAGS = 25


class Params(ActionParams):
    confirm: bool = False
    limit: int = 0
    """0 means every candidate."""


def _candidates(conn, limit: int) -> list:
    """Files with tags now, or tags written last time. Trashed ones excluded."""
    sql = (
        "SELECT drive_id, name, synced_tags FROM drive_files "
        "WHERE trashed_at IS NULL AND ("
        "  drive_id IN (SELECT drive_id FROM file_tags) "
        "  OR (synced_tags IS NOT NULL AND synced_tags != '')"
        ") ORDER BY parent_path, name"
    )
    if limit > 0:
        return list(conn.execute(f"{sql} LIMIT ?", (limit,)))
    return list(conn.execute(sql))


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    desired_by_file = TagsRepo(ctx.conn).slugs_by_file()
    rows = _candidates(ctx.conn, params.limit)
    if not rows:
        yield ProgressEvent(
            "No tagged files to sync. Tag something on the Library page first.",
            progress=1.0,
        )
        return

    yield ProgressEvent(f"Examining {len(rows)} file(s).", progress=0.0)

    plans: list[tuple[str, str, set[str], set[str], set[str]]] = []
    over_budget = 0
    for index, row in enumerate(rows, start=1):
        drive_id, name = row["drive_id"], row["name"]
        desired = desired_by_file.get(drive_id, set())

        if len(desired) > MAX_TAGS:
            over_budget += 1
            yield ProgressEvent(
                f"{name}: {len(desired)} tags exceeds the {MAX_TAGS} that fit in "
                f"Drive's appProperties. Skipping — remove some tags first.",
                level="warn",
            )
            continue

        try:
            current_props = ctx.drive.app_properties(drive_id)
        except DriveError as exc:
            yield ProgressEvent(f"{name}: cannot read properties: {exc}",
                                level="error")
            continue

        current = {
            key[len(PREFIX):] for key in current_props if key.startswith(PREFIX)
        }
        adds, removes = desired - current, current - desired
        if adds or removes:
            plans.append((drive_id, name, desired, adds, removes))

        if index % 50 == 0:
            yield ProgressEvent(
                f"Examined {index} of {len(rows)}.",
                progress=0.5 * index / len(rows),
            )

    if not plans:
        yield ProgressEvent(
            f"0 file(s) to change — Drive already matches the catalog."
            + (f" {over_budget} skipped as over budget." if over_budget else ""),
            progress=1.0,
        )
        return

    added = sum(len(plan[3]) for plan in plans)
    removed = sum(len(plan[4]) for plan in plans)
    yield ProgressEvent(
        f"{len(plans)} file(s) differ: {added} tag(s) to add, "
        f"{removed} to remove.",
        progress=0.5,
    )
    for _, name, _, adds, removes in plans[:50]:
        for slug in sorted(adds):
            yield ProgressEvent(f"would add {PREFIX}{slug} to {name}")
        for slug in sorted(removes):
            yield ProgressEvent(f"would remove {PREFIX}{slug} from {name}")

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — Drive was not changed. Re-run with confirm to "
            f"update {len(plans)} file(s).",
            progress=1.0,
            level="warn",
        )
        return

    changed = 0
    for index, (drive_id, name, desired, adds, removes) in enumerate(plans, start=1):
        properties: dict[str, str | None] = {
            f"{PREFIX}{slug}": "1" for slug in adds
        }
        # None is how the Drive API deletes a property.
        properties.update({f"{PREFIX}{slug}": None for slug in removes})
        try:
            ctx.writer.update_properties(drive_id, properties)
        except DriveError as exc:
            yield ProgressEvent(f"{name}: {exc}", level="error")
            continue

        ctx.conn.execute(
            "UPDATE drive_files SET synced_tags = ? WHERE drive_id = ?",
            (",".join(sorted(desired)), drive_id),
        )
        ctx.conn.commit()
        changed += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Updated {index} of {len(plans)}.",
                progress=0.5 + 0.5 * index / len(plans),
            )

    yield ProgressEvent(
        f"Updated {changed} file(s) on Drive: {added} tag(s) added, "
        f"{removed} removed.",
        progress=1.0,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_action_sync_tags.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Confirm the registry discovered it**

Run: `uv run pytest tests/test_actions.py tests/test_api_actions.py -v`
Expected: PASS. If a test asserts on the exact list of action ids, add `sync_tags` to it — the registry is meant to pick this up with no other change.

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add photolib/actions/sync_tags.py tests/test_action_sync_tags.py
git commit -m "feat(actions): sync_tags — mirror catalog tags onto Drive, dry run by default"
```

---

## Task 10: Frontend types and API client

**Files:**
- Modify: `web/src/api/types.ts` (append)
- Modify: `web/src/api/client.ts` (append)
- Test: `web/src/api/client.test.ts` (append)

**Interfaces:**
- Consumes: the routes from Tasks 7 and 8.
- Produces:

```ts
export interface Tag { id: number; name: string; slug: string; color: string }
export interface TagWithCount extends Tag { file_count: number }
export interface LibraryFile {
  drive_id: string; name: string; month: string; mime_type: string | null
  media_type: 'image' | 'video' | 'other'; size: number | null; md5: string | null
  capture_time: number | null; capture_source: string | null
  place: string | null; country: string | null
  duplicate_of: string | null; duplicate_reason: string | null
  archive_name: string | null; tags: Tag[]
}
export interface Facet { value: string; count: number }
export interface Facets {
  total: number; months: Facet[]; places: Facet[]; countries: Facet[]
  types: Facet[]; duplicates: number
}

export const listLibraryFiles: (f: LibraryFilters, page?: {limit?: number; offset?: number}) => Promise<{total: number; rows: LibraryFile[]}>
export const listLibraryIds: (f: LibraryFilters) => Promise<string[]>
export const getFacets: () => Promise<Facets>
export const getLibraryFile: (driveId: string) => Promise<LibraryFile>
export const thumbUrl: (driveId: string, size?: 400 | 1600) => string
export const listTags: () => Promise<TagWithCount[]>
export const createTag: (name: string, color?: string) => Promise<TagWithCount>
export const patchTag: (id: number, patch: {name?: string; color?: string}) => Promise<TagWithCount>
export const deleteTag: (id: number) => Promise<{deleted: number}>
export const mergeTags: (sourceId: number, targetId: number) => Promise<{moved: number; target: TagWithCount}>
export const addFilesToTag: (id: number, driveIds: string[]) => Promise<{added: number}>
export const removeFilesFromTag: (id: number, driveIds: string[]) => Promise<{removed: number}>
```

`LibraryFilters` and its `toQuery` come from Task 11 — write that task first if you are working strictly in order, or import the type and let TypeScript complain until Task 11 lands. The two tasks are split because `filters.ts` is pure logic with its own tests and `client.ts` is transport.

- [ ] **Step 1: Write the failing tests**

Append to `web/src/api/client.test.ts`:

```ts
describe('library client', () => {
  it('builds a filtered files URL', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ total: 0, rows: [] })))
    vi.stubGlobal('fetch', fetchMock)

    await listLibraryFiles({ month: '2025-05', mediaType: 'video' }, { limit: 50 })

    const url = String(fetchMock.mock.calls[0][0])
    expect(url).toContain('/api/library/files?')
    expect(url).toContain('month=2025-05')
    expect(url).toContain('media_type=video')
    expect(url).toContain('limit=50')
  })

  it('omits filters that are not set', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ total: 0, rows: [] })))
    vi.stubGlobal('fetch', fetchMock)

    await listLibraryFiles({})

    expect(String(fetchMock.mock.calls[0][0])).not.toContain('month=')
  })

  it('unwraps the ids envelope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ids: ['a', 'b'] }))))
    expect(await listLibraryIds({})).toEqual(['a', 'b'])
  })

  it('builds a thumbnail URL at the grid size by default', () => {
    expect(thumbUrl('d1')).toBe('/api/thumb/d1?size=400')
    expect(thumbUrl('d1', 1600)).toBe('/api/thumb/d1?size=1600')
  })

  it('escapes a drive id in the thumbnail URL', () => {
    expect(thumbUrl('a/b')).toBe('/api/thumb/a%2Fb?size=400')
  })

  it('posts drive ids when tagging in bulk', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ added: 2 })))
    vi.stubGlobal('fetch', fetchMock)

    await addFilesToTag(7, ['d1', 'd2'])

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/tags/7/files')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ drive_ids: ['d1', 'd2'] })
  })

  it('removes through a POST, because a DELETE body is not dependable', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ removed: 1 })))
    vi.stubGlobal('fetch', fetchMock)

    await removeFilesFromTag(7, ['d1'])

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/tags/7/files/remove')
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('POST')
  })
})
```

Add the new names to the import at the top of the file, and `vi` to the vitest import if it is not already there. Follow whatever `afterEach` teardown the existing tests use; if there is none, add `afterEach(() => vi.unstubAllGlobals())`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — `listLibraryFiles is not defined`.

- [ ] **Step 3: Add the types**

Append to `web/src/api/types.ts`:

```ts
export interface Tag {
  id: number
  name: string
  slug: string
  color: string
}

export interface TagWithCount extends Tag {
  file_count: number
}

export type MediaType = 'image' | 'video' | 'other'

export interface LibraryFile {
  drive_id: string
  name: string
  month: string
  mime_type: string | null
  media_type: MediaType
  size: number | null
  md5: string | null
  capture_time: number | null
  capture_source: string | null
  place: string | null
  country: string | null
  duplicate_of: string | null
  duplicate_reason: string | null
  archive_name: string | null
  tags: Tag[]
}

export interface Facet {
  value: string
  count: number
}

export interface Facets {
  total: number
  months: Facet[]
  places: Facet[]
  countries: Facet[]
  types: Facet[]
  duplicates: number
}
```

- [ ] **Step 4: Add the client functions**

Append to `web/src/api/client.ts`, and extend its `import type` block with exactly `Facets`, `LibraryFile`, `TagWithCount` — not `Tag`, which nothing in this file references and which `tsc` would flag as unused:

```ts
import { toQuery, type LibraryFilters } from '../lib/filters'

export const listLibraryFiles = (
  filters: LibraryFilters,
  page: { limit?: number; offset?: number } = {},
) => {
  const params = toQuery(filters)
  if (page.limit !== undefined) params.set('limit', String(page.limit))
  if (page.offset !== undefined) params.set('offset', String(page.offset))
  return request<{ total: number; rows: LibraryFile[] }>(
    `/api/library/files?${params.toString()}`,
  )
}

export const listLibraryIds = (filters: LibraryFilters) =>
  request<{ ids: string[] }>(`/api/library/ids?${toQuery(filters).toString()}`)
    .then((body) => body.ids)

export const getFacets = () => request<Facets>('/api/library/facets')

export const getLibraryFile = (driveId: string) =>
  request<LibraryFile>(`/api/library/file/${encodeURIComponent(driveId)}`)

/** The backend proxies Drive's render; Chrome cannot decode HEIC itself. */
export const thumbUrl = (driveId: string, size: 400 | 1600 = 400) =>
  `/api/thumb/${encodeURIComponent(driveId)}?size=${size}`

export const listTags = () => request<TagWithCount[]>('/api/tags')

export const createTag = (name: string, color?: string) =>
  request<TagWithCount>('/api/tags', {
    method: 'POST',
    body: JSON.stringify(color ? { name, color } : { name }),
  })

export const patchTag = (id: number, patch: { name?: string; color?: string }) =>
  request<TagWithCount>(`/api/tags/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export const deleteTag = (id: number) =>
  request<{ deleted: number }>(`/api/tags/${id}`, { method: 'DELETE' })

export const mergeTags = (sourceId: number, targetId: number) =>
  request<{ moved: number; target: TagWithCount }>('/api/tags/merge', {
    method: 'POST',
    body: JSON.stringify({ source_id: sourceId, target_id: targetId }),
  })

export const addFilesToTag = (id: number, driveIds: string[]) =>
  request<{ added: number }>(`/api/tags/${id}/files`, {
    method: 'POST',
    body: JSON.stringify({ drive_ids: driveIds }),
  })

/** A POST, not a DELETE with a body: 1,284 ids do not fit in a URL. */
export const removeFilesFromTag = (id: number, driveIds: string[]) =>
  request<{ removed: number }>(`/api/tags/${id}/files/remove`, {
    method: 'POST',
    body: JSON.stringify({ drive_ids: driveIds }),
  })
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS. (Requires Task 11's `filters.ts`; do that task first if this fails to resolve the import.)

- [ ] **Step 6: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/api/client.test.ts
git commit -m "feat(web): library and tag API client"
```

---

## Task 11: Filter and selection logic

**Files:**
- Create: `web/src/lib/filters.ts`
- Create: `web/src/lib/selection.ts`
- Test: `web/src/lib/filters.test.ts`, `web/src/lib/selection.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:

```ts
// filters.ts
export interface LibraryFilters {
  month?: string; place?: string; country?: string
  mediaType?: MediaType; tagId?: number; duplicates?: boolean; search?: string
}
export const EMPTY_FILTERS: LibraryFilters
export function toQuery(filters: LibraryFilters): URLSearchParams
export function isEmpty(filters: LibraryFilters): boolean
export function describe(filters: LibraryFilters): string[]

// selection.ts
export interface Selection { anchor: string | null; ids: ReadonlySet<string> }
export const NO_SELECTION: Selection
export interface ClickModifiers { shift: boolean; meta: boolean }
export function click(state: Selection, id: string, ordered: string[], mods: ClickModifiers): Selection
export function selectAll(ids: string[]): Selection
export function clear(): Selection
export function isSelected(state: Selection, id: string): boolean
```

Both files are pure — no React, no fetch — because selection maths is where a grid goes subtly wrong and it deserves tests that run in a millisecond.

Selection rules, matching what every file manager does: a plain click replaces the selection with one file and moves the anchor there. ⌘-click toggles one file and moves the anchor there. Shift-click adds the inclusive range between the anchor and the clicked file to the existing selection and leaves the anchor alone, so repeated shift-clicks grow and re-aim from the same origin. Shift-click with no anchor behaves as a plain click.

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/filters.test.ts`:

```ts
import { describe as group, expect, it } from 'vitest'
import { EMPTY_FILTERS, describe, isEmpty, toQuery } from './filters'

group('toQuery', () => {
  it('is empty for no filters', () => {
    expect(toQuery(EMPTY_FILTERS).toString()).toBe('')
  })

  it('maps camelCase to the API snake_case', () => {
    const query = toQuery({ mediaType: 'image', tagId: 3 })
    expect(query.get('media_type')).toBe('image')
    expect(query.get('tag_id')).toBe('3')
  })

  it('sends duplicates only when true', () => {
    expect(toQuery({ duplicates: true }).get('duplicates')).toBe('true')
    expect(toQuery({ duplicates: false }).has('duplicates')).toBe(false)
  })

  it('drops an empty search string', () => {
    expect(toQuery({ search: '' }).has('search')).toBe(false)
    expect(toQuery({ search: 'img' }).get('search')).toBe('img')
  })

  it('keeps a tag id of zero out, since ids start at one', () => {
    expect(toQuery({ tagId: undefined }).has('tag_id')).toBe(false)
  })
})

group('isEmpty', () => {
  it('is true for no filters', () => {
    expect(isEmpty(EMPTY_FILTERS)).toBe(true)
  })

  it('is false once anything is set', () => {
    expect(isEmpty({ month: '2025-05' })).toBe(false)
    expect(isEmpty({ duplicates: true })).toBe(false)
  })

  it('ignores a false duplicates flag', () => {
    expect(isEmpty({ duplicates: false })).toBe(true)
  })
})

group('describe', () => {
  it('names each active filter for the chip row', () => {
    expect(describe({ month: '2025-05', mediaType: 'video', duplicates: true }))
      .toEqual(['2025-05', 'video', 'duplicates'])
  })

  it('is empty when nothing is filtered', () => {
    expect(describe(EMPTY_FILTERS)).toEqual([])
  })
})
```

Create `web/src/lib/selection.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { NO_SELECTION, click, clear, isSelected, selectAll } from './selection'

const ORDER = ['a', 'b', 'c', 'd', 'e']
const plain = { shift: false, meta: false }
const shift = { shift: true, meta: false }
const meta = { shift: false, meta: true }

describe('click', () => {
  it('selects one file and anchors there', () => {
    const state = click(NO_SELECTION, 'b', ORDER, plain)
    expect([...state.ids]).toEqual(['b'])
    expect(state.anchor).toBe('b')
  })

  it('replaces the previous selection', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, plain)
    expect([...state.ids]).toEqual(['d'])
  })
})

describe('meta-click', () => {
  it('adds without clearing', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, meta)
    expect([...state.ids].sort()).toEqual(['b', 'd'])
  })

  it('toggles a selected file off', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'b', ORDER, meta)
    expect([...state.ids]).toEqual([])
  })

  it('moves the anchor', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, meta)
    expect(state.anchor).toBe('d')
  })
})

describe('shift-click', () => {
  it('selects the inclusive range forwards', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('selects the inclusive range backwards', () => {
    let state = click(NO_SELECTION, 'd', ORDER, plain)
    state = click(state, 'b', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('leaves the anchor where it was, so the range can be re-aimed', () => {
    let state = click(NO_SELECTION, 'b', ORDER, plain)
    state = click(state, 'd', ORDER, shift)
    state = click(state, 'c', ORDER, shift)
    expect(state.anchor).toBe('b')
    expect([...state.ids].sort()).toEqual(['b', 'c', 'd'])
  })

  it('keeps what was already selected', () => {
    let state = click(NO_SELECTION, 'a', ORDER, plain)
    state = click(state, 'c', ORDER, meta)
    state = click(state, 'e', ORDER, shift)
    expect([...state.ids].sort()).toEqual(['a', 'c', 'd', 'e'])
  })

  it('behaves like a plain click when there is no anchor', () => {
    const state = click(NO_SELECTION, 'c', ORDER, shift)
    expect([...state.ids]).toEqual(['c'])
    expect(state.anchor).toBe('c')
  })

  it('behaves like a plain click when the anchor has been filtered away', () => {
    const orphaned = { anchor: 'zzz', ids: new Set(['zzz']) }
    const state = click(orphaned, 'c', ORDER, shift)
    expect([...state.ids]).toEqual(['c'])
  })
})

describe('selectAll and clear', () => {
  it('selects every id given', () => {
    expect([...selectAll(ORDER).ids]).toEqual(ORDER)
  })

  it('anchors on the first', () => {
    expect(selectAll(ORDER).anchor).toBe('a')
  })

  it('handles an empty result set', () => {
    expect(selectAll([]).anchor).toBe(null)
  })

  it('clears', () => {
    expect([...clear().ids]).toEqual([])
  })
})

describe('isSelected', () => {
  it('answers for a member and a stranger', () => {
    const state = click(NO_SELECTION, 'b', ORDER, plain)
    expect(isSelected(state, 'b')).toBe(true)
    expect(isSelected(state, 'a')).toBe(false)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./filters` or `./selection`.

- [ ] **Step 3: Write `filters.ts`**

Create `web/src/lib/filters.ts`:

```ts
import type { MediaType } from '../api/types'

/**
 * What the Library is currently showing. Kept as plain data with no React in
 * sight, so the URL builder can be tested on its own — the backend reads
 * snake_case and the UI writes camelCase, and that seam is easy to get wrong.
 */
export interface LibraryFilters {
  month?: string
  place?: string
  country?: string
  mediaType?: MediaType
  tagId?: number
  duplicates?: boolean
  search?: string
}

export const EMPTY_FILTERS: LibraryFilters = {}

export function toQuery(filters: LibraryFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.month) params.set('month', filters.month)
  if (filters.place) params.set('place', filters.place)
  if (filters.country) params.set('country', filters.country)
  if (filters.mediaType) params.set('media_type', filters.mediaType)
  if (filters.tagId !== undefined) params.set('tag_id', String(filters.tagId))
  if (filters.duplicates) params.set('duplicates', 'true')
  if (filters.search) params.set('search', filters.search)
  return params
}

export function isEmpty(filters: LibraryFilters): boolean {
  return toQuery(filters).toString() === ''
}

/** One short label per active filter, for the chip row above the grid. */
export function describe(filters: LibraryFilters): string[] {
  const labels: string[] = []
  if (filters.month) labels.push(filters.month)
  if (filters.place) labels.push(filters.place)
  if (filters.country) labels.push(filters.country)
  if (filters.mediaType) labels.push(filters.mediaType)
  if (filters.duplicates) labels.push('duplicates')
  if (filters.search) labels.push(`"${filters.search}"`)
  return labels
}
```

`describe` deliberately omits `tagId`, because a numeric id means nothing to a reader. The Library page resolves it to a tag name and renders that chip itself.

- [ ] **Step 4: Write `selection.ts`**

Create `web/src/lib/selection.ts`:

```ts
/**
 * Grid selection maths, kept away from React.
 *
 * The rules are the ones every file manager uses: plain click replaces, meta
 * toggles, shift extends from the anchor. The anchor is what makes repeated
 * shift-clicks feel right — it stays put so the range can be re-aimed, rather
 * than walking forward with each click.
 */

export interface Selection {
  anchor: string | null
  ids: ReadonlySet<string>
}

export const NO_SELECTION: Selection = { anchor: null, ids: new Set() }

export interface ClickModifiers {
  shift: boolean
  meta: boolean
}

export function click(
  state: Selection,
  id: string,
  ordered: string[],
  mods: ClickModifiers,
): Selection {
  if (mods.meta) {
    const ids = new Set(state.ids)
    if (ids.has(id)) ids.delete(id)
    else ids.add(id)
    return { anchor: id, ids }
  }

  if (mods.shift && state.anchor !== null) {
    const from = ordered.indexOf(state.anchor)
    const to = ordered.indexOf(id)
    // The anchor can be filtered out from under us; fall back to a plain click
    // rather than selecting a nonsensical range.
    if (from !== -1 && to !== -1) {
      const [start, end] = from <= to ? [from, to] : [to, from]
      const ids = new Set(state.ids)
      for (const each of ordered.slice(start, end + 1)) ids.add(each)
      return { anchor: state.anchor, ids }
    }
  }

  return { anchor: id, ids: new Set([id]) }
}

/** Everything matching the current filter, not just the rendered rows. */
export function selectAll(ids: string[]): Selection {
  return { anchor: ids[0] ?? null, ids: new Set(ids) }
}

export function clear(): Selection {
  return { anchor: null, ids: new Set() }
}

export function isSelected(state: Selection, id: string): boolean {
  return state.ids.has(id)
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS — `filters.test.ts` 10 tests, `selection.test.ts` 15 tests, plus Task 10's client tests now that the import resolves.

- [ ] **Step 6: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/filters.ts web/src/lib/selection.ts \
        web/src/lib/filters.test.ts web/src/lib/selection.test.ts
git commit -m "feat(web): filter query building and grid selection maths"
```

---

## Task 12: Thumb and FilterSidebar components

**Files:**
- Create: `web/src/components/Thumb.tsx`
- Create: `web/src/components/FilterSidebar.tsx`
- Test: `web/src/components/Thumb.test.tsx`, `web/src/components/FilterSidebar.test.tsx`

**Interfaces:**
- Consumes: `thumbUrl` (Task 10); `Facets`, `TagWithCount`, `LibraryFilters` (Tasks 10, 11).
- Produces:

```tsx
export function Thumb(props: {
  driveId: string; name: string; size?: 400 | 1600; className?: string
}): JSX.Element

export function FilterSidebar(props: {
  facets: Facets | null
  tags: TagWithCount[]
  filters: LibraryFilters
  onChange: (next: LibraryFilters) => void
}): JSX.Element
```

`Thumb` exists because `/api/thumb/{id}` answers **202** for a file Drive has not rendered yet — normal for a few minutes after Organize. A bare `<img>` treats that as a broken image forever. `Thumb` catches the error, shows the file extension as a placeholder, and retries a few times with a widening delay before giving up. `loading="lazy"` is what keeps 1,284 tiles from firing 1,284 requests at once; there is no virtualisation library, and this is why none is needed.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Thumb.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Thumb } from './Thumb'

vi.mock('../api/client', () => ({
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
}))

describe('Thumb', () => {
  it('renders an image at the grid size by default', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    const image = screen.getByRole('img') as HTMLImageElement
    expect(image.getAttribute('src')).toContain('/api/thumb/d1?size=400')
  })

  it('asks for the large render when told to', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" size={1600} />)
    expect(screen.getByRole('img').getAttribute('src')).toContain('size=1600')
  })

  it('defers loading, which is what replaces virtualisation', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    expect(screen.getByRole('img').getAttribute('loading')).toBe('lazy')
  })

  it('names the file for screen readers', () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    expect(screen.getByRole('img').getAttribute('alt')).toBe('IMG_1.HEIC')
  })

  it('falls back to the extension when Drive has no render yet', async () => {
    render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
    screen.getByRole('img').dispatchEvent(new Event('error'))
    expect(await screen.findByText('HEIC')).toBeTruthy()
  })

  it('retries rather than giving up on the first miss', async () => {
    vi.useFakeTimers()
    try {
      render(<Thumb driveId="d1" name="IMG_1.HEIC" />)
      const first = screen.getByRole('img').getAttribute('src')
      screen.getByRole('img').dispatchEvent(new Event('error'))
      await vi.advanceTimersByTimeAsync(5000)
      const image = screen.queryByRole('img')
      expect(image?.getAttribute('src')).not.toBe(first)
    } finally {
      vi.useRealTimers()
    }
  })
})
```

Create `web/src/components/FilterSidebar.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FilterSidebar } from './FilterSidebar'

const FACETS = {
  total: 12,
  months: [
    { value: '2025-06', count: 7 },
    { value: '2025-05', count: 5 },
  ],
  places: [{ value: 'Warsaw', count: 4 }],
  countries: [{ value: 'Poland', count: 4 }],
  types: [
    { value: 'image', count: 9 },
    { value: 'video', count: 3 },
  ],
  duplicates: 2,
}

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 3 },
]

function setup(filters = {}) {
  const onChange = vi.fn()
  render(
    <FilterSidebar facets={FACETS} tags={TAGS} filters={filters} onChange={onChange} />,
  )
  return onChange
}

describe('FilterSidebar', () => {
  it('lists months with their counts', () => {
    setup()
    expect(screen.getByRole('button', { name: /2025-06/ }).textContent).toContain('7')
  })

  it('lists places, types, and tags', () => {
    setup()
    expect(screen.getByRole('button', { name: /Warsaw/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /video/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Family/ })).toBeTruthy()
  })

  it('offers duplicates as one more filter, not a separate page', () => {
    setup()
    expect(screen.getByRole('button', { name: /duplicates/i }).textContent).toContain('2')
  })

  it('applies a month', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    expect(onChange).toHaveBeenCalledWith({ month: '2025-05' })
  })

  it('applies a tag by id', async () => {
    const onChange = setup()
    await userEvent.click(screen.getByRole('button', { name: /Family/ }))
    expect(onChange).toHaveBeenCalledWith({ tagId: 1 })
  })

  it('clears a filter when its active value is clicked again', async () => {
    const onChange = setup({ month: '2025-05' })
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    expect(onChange).toHaveBeenCalledWith({ month: undefined })
  })

  it('keeps other filters when one changes', async () => {
    const onChange = setup({ month: '2025-05' })
    await userEvent.click(screen.getByRole('button', { name: /video/ }))
    expect(onChange).toHaveBeenCalledWith({ month: '2025-05', mediaType: 'video' })
  })

  it('marks the active filter', () => {
    setup({ month: '2025-05' })
    expect(
      screen.getByRole('button', { name: /2025-05/ }).getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('searches by name', async () => {
    const onChange = setup()
    await userEvent.type(screen.getByLabelText(/search/i), 'IMG')
    expect(onChange).toHaveBeenLastCalledWith({ search: 'IMG' })
  })

  it('offers a way back to everything', async () => {
    const onChange = setup({ month: '2025-05', duplicates: true })
    await userEvent.click(screen.getByRole('button', { name: /clear filters/i }))
    expect(onChange).toHaveBeenCalledWith({})
  })

  it('says so plainly before the first scan', () => {
    render(
      <FilterSidebar facets={null} tags={[]} filters={{}} onChange={vi.fn()} />,
    )
    expect(screen.getByText(/run scan/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./Thumb` or `./FilterSidebar`.

- [ ] **Step 3: Write `Thumb.tsx`**

Create `web/src/components/Thumb.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { thumbUrl } from '../api/client'

// Drive renders thumbnails a little after upload, so the proxy answers 202 for
// a while. Three widening retries covers that without hammering the backend.
const RETRY_DELAYS = [4000, 15000, 60000]

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot === -1 ? 'FILE' : name.slice(dot + 1).toUpperCase()
}

export function Thumb({
  driveId,
  name,
  size = 400,
  className,
}: {
  driveId: string
  name: string
  size?: 400 | 1600
  className?: string
}) {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setAttempt(0)
    setFailed(false)
  }, [driveId, size])

  useEffect(() => {
    if (!failed) return
    const delay = RETRY_DELAYS[attempt]
    if (delay === undefined) return          // out of retries; the placeholder stays
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1)
      setFailed(false)
    }, delay)
    return () => clearTimeout(timer)
  }, [failed, attempt])

  if (failed && attempt >= RETRY_DELAYS.length) {
    return (
      <span className={`thumb thumb-missing ${className ?? ''}`} title={name}>
        {extensionOf(name)}
      </span>
    )
  }

  if (failed) {
    return (
      <span className={`thumb thumb-pending ${className ?? ''}`} title={name}>
        {extensionOf(name)}
      </span>
    )
  }

  return (
    <img
      className={`thumb ${className ?? ''}`}
      // `loading="lazy"` is what lets 1,284 tiles render without a
      // virtualisation library: the browser fetches only what is on screen.
      loading="lazy"
      src={attempt === 0 ? thumbUrl(driveId, size) : `${thumbUrl(driveId, size)}&try=${attempt}`}
      alt={name}
      onError={() => setFailed(true)}
    />
  )
}
```

- [ ] **Step 4: Write `FilterSidebar.tsx`**

Create `web/src/components/FilterSidebar.tsx`:

```tsx
import type { Facet, Facets, TagWithCount } from '../api/types'
import type { LibraryFilters } from '../lib/filters'
import { isEmpty } from '../lib/filters'

function FacetList({
  title,
  facets,
  active,
  onPick,
}: {
  title: string
  facets: Facet[]
  active: string | undefined
  onPick: (value: string | undefined) => void
}) {
  if (facets.length === 0) return null
  return (
    <section className="facet">
      <h3>{title}</h3>
      {facets.map((facet) => (
        <button
          key={facet.value}
          type="button"
          aria-pressed={active === facet.value}
          className={active === facet.value ? 'facet-item active' : 'facet-item'}
          // Clicking the active value again is how you get back to everything.
          onClick={() => onPick(active === facet.value ? undefined : facet.value)}
        >
          <span>{facet.value}</span>
          <span className="count">{facet.count}</span>
        </button>
      ))}
    </section>
  )
}

export function FilterSidebar({
  facets,
  tags,
  filters,
  onChange,
}: {
  facets: Facets | null
  tags: TagWithCount[]
  filters: LibraryFilters
  onChange: (next: LibraryFilters) => void
}) {
  if (facets === null || facets.total === 0) {
    return (
      <aside className="filters">
        <p className="muted">
          Nothing here yet. Run Scan Archives to index the destination, then
          Organize Photos to fill it.
        </p>
      </aside>
    )
  }

  const set = (patch: Partial<LibraryFilters>) => onChange({ ...filters, ...patch })

  return (
    <aside className="filters">
      <label>
        Search
        <input
          type="search"
          value={filters.search ?? ''}
          onChange={(event) => set({ search: event.target.value || undefined })}
        />
      </label>

      <FacetList
        title="Month"
        facets={facets.months}
        active={filters.month}
        onPick={(month) => set({ month })}
      />
      <FacetList
        title="Type"
        facets={facets.types}
        active={filters.mediaType}
        onPick={(value) => set({ mediaType: value as LibraryFilters['mediaType'] })}
      />
      <FacetList
        title="Place"
        facets={facets.places}
        active={filters.place}
        onPick={(place) => set({ place })}
      />
      <FacetList
        title="Country"
        facets={facets.countries}
        active={filters.country}
        onPick={(country) => set({ country })}
      />

      {tags.length > 0 && (
        <section className="facet">
          <h3>Tag</h3>
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              aria-pressed={filters.tagId === tag.id}
              className={filters.tagId === tag.id ? 'facet-item active' : 'facet-item'}
              onClick={() =>
                set({ tagId: filters.tagId === tag.id ? undefined : tag.id })
              }
            >
              <span className="swatch" style={{ background: tag.color }} />
              <span>{tag.name}</span>
              <span className="count">{tag.file_count}</span>
            </button>
          ))}
        </section>
      )}

      {facets.duplicates > 0 && (
        <section className="facet">
          <h3>Flagged</h3>
          <button
            type="button"
            aria-pressed={filters.duplicates === true}
            className={filters.duplicates ? 'facet-item active' : 'facet-item'}
            onClick={() => set({ duplicates: filters.duplicates ? undefined : true })}
          >
            <span>duplicates</span>
            <span className="count">{facets.duplicates}</span>
          </button>
        </section>
      )}

      {!isEmpty(filters) && (
        <button type="button" className="link" onClick={() => onChange({})}>
          Clear filters
        </button>
      )}
    </aside>
  )
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS — 6 `Thumb` tests, 11 `FilterSidebar` tests.

- [ ] **Step 6: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/Thumb.tsx web/src/components/FilterSidebar.tsx \
        web/src/components/Thumb.test.tsx web/src/components/FilterSidebar.test.tsx
git commit -m "feat(web): thumbnail tile with retry, and the library filter sidebar"
```

---

## Task 13: The Library page

**Files:**
- Create: `web/src/pages/LibraryPage.tsx`
- Test: `web/src/pages/LibraryPage.test.tsx`

**Interfaces:**
- Consumes: `listLibraryFiles`, `listLibraryIds`, `getFacets`, `listTags` (Task 10); `Selection` helpers (Task 11); `Thumb`, `FilterSidebar` (Task 12).
- Produces: `export function LibraryPage(): JSX.Element`. The bulk tag toolbar arrives in Task 14 and the lightbox in Task 15; this task leaves a clearly marked seam for each.

Files come back ordered newest month first, so grouping is a single pass over the rows rather than a sort. Paging is "Load more" against `offset`, which keeps the grid honest about how much it has actually fetched.

- [ ] **Step 1: Write the failing test**

Create `web/src/pages/LibraryPage.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LibraryPage } from './LibraryPage'

const ROWS = [
  {
    drive_id: 'd1', name: 'IMG_1.HEIC', month: '2025-06', mime_type: 'image/heic',
    media_type: 'image', size: 100, md5: 'a', capture_time: 1750000000,
    capture_source: 'photo_taken_time', place: 'Warsaw', country: 'Poland',
    duplicate_of: null, duplicate_reason: null, archive_name: 'part-001.zip',
    tags: [{ id: 1, name: 'Family', slug: 'family', color: '#f00' }],
  },
  {
    drive_id: 'd2', name: 'VID_1.MOV', month: '2025-06', mime_type: 'video/quicktime',
    media_type: 'video', size: 200, md5: 'b', capture_time: null,
    capture_source: null, place: null, country: null,
    duplicate_of: '2025-06', duplicate_reason: 'name and size match an existing file',
    archive_name: 'part-002.zip', tags: [],
  },
  {
    drive_id: 'd3', name: 'IMG_9.HEIC', month: '2025-05', mime_type: 'image/heic',
    media_type: 'image', size: 300, md5: 'c', capture_time: 1747000000,
    capture_source: 'exif', place: 'Lisbon', country: 'Portugal',
    duplicate_of: null, duplicate_reason: null, archive_name: 'part-003.zip',
    tags: [],
  },
]

const listLibraryFiles = vi.fn(async () => ({ total: 3, rows: ROWS }))
const listLibraryIds = vi.fn(async () => ['d1', 'd2', 'd3'])

vi.mock('../api/client', () => ({
  listLibraryFiles: (...args: unknown[]) => listLibraryFiles(...(args as [])),
  listLibraryIds: (...args: unknown[]) => listLibraryIds(...(args as [])),
  getFacets: vi.fn(async () => ({
    total: 3,
    months: [{ value: '2025-06', count: 2 }, { value: '2025-05', count: 1 }],
    places: [{ value: 'Warsaw', count: 1 }],
    countries: [{ value: 'Poland', count: 1 }],
    types: [{ value: 'image', count: 2 }, { value: 'video', count: 1 }],
    duplicates: 1,
  })),
  listTags: vi.fn(async () => [
    { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 1 },
  ]),
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
  getLibraryFile: vi.fn(async () => ROWS[0]),
  addFilesToTag: vi.fn(async () => ({ added: 1 })),
  removeFilesFromTag: vi.fn(async () => ({ removed: 1 })),
  createTag: vi.fn(async () => ({
    id: 2, name: 'New', slug: 'new', color: '#000', file_count: 0,
  })),
}))

afterEach(() => vi.clearAllMocks())

const tile = (name: string) => screen.getByRole('img', { name }).closest('.tile') as HTMLElement

describe('LibraryPage', () => {
  it('groups files under their month', async () => {
    render(<LibraryPage />)
    expect(await screen.findByRole('heading', { name: '2025-06' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '2025-05' })).toBeTruthy()
  })

  it('renders a tile per file', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.getAllByRole('img')).toHaveLength(3)
  })

  it('reports how many files are showing', async () => {
    render(<LibraryPage />)
    expect(await screen.findByText(/3 file/i)).toBeTruthy()
  })

  it('marks a flagged duplicate', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'VID_1.MOV' })
    expect(tile('VID_1.MOV').textContent).toMatch(/duplicate/i)
  })

  it('selects a file on click', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(tile('IMG_1.HEIC').getAttribute('aria-selected')).toBe('true')
  })

  it('extends the selection with shift-click', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('img', { name: 'IMG_9.HEIC' }), {
      shiftKey: true,
    })
    expect(tile('VID_1.MOV').getAttribute('aria-selected')).toBe('true')
  })

  it('selects everything matching the filter, not just what is rendered', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('button', { name: /select all/i }))
    await waitFor(() => expect(listLibraryIds).toHaveBeenCalled())
    expect(await screen.findByText(/3 selected/i)).toBeTruthy()
  })

  it('refetches when a filter changes', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    await waitFor(() =>
      expect(listLibraryFiles).toHaveBeenLastCalledWith(
        expect.objectContaining({ month: '2025-05' }),
        expect.anything(),
      ),
    )
  })

  it('drops the selection when the filter changes', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('button', { name: /2025-05/ }))
    await waitFor(() => expect(screen.queryByText(/selected/i)).toBeNull())
  })

  it('offers Load more only while there is more', async () => {
    listLibraryFiles.mockResolvedValueOnce({ total: 500, rows: ROWS })
    render(<LibraryPage />)
    expect(await screen.findByRole('button', { name: /load more/i })).toBeTruthy()
  })

  it('does not offer Load more once everything is shown', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.queryByRole('button', { name: /load more/i })).toBeNull()
  })

  it('says so plainly when a filter matches nothing', async () => {
    listLibraryFiles.mockResolvedValueOnce({ total: 0, rows: [] })
    render(<LibraryPage />)
    expect(await screen.findByText(/no files match/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./LibraryPage`.

- [ ] **Step 3: Write the page**

Create `web/src/pages/LibraryPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react'
import { getFacets, listLibraryFiles, listLibraryIds, listTags } from '../api/client'
import type { Facets, LibraryFile, TagWithCount } from '../api/types'
import { FilterSidebar } from '../components/FilterSidebar'
import { Thumb } from '../components/Thumb'
import type { LibraryFilters } from '../lib/filters'
import { NO_SELECTION, click, clear, isSelected, selectAll } from '../lib/selection'
import type { Selection } from '../lib/selection'

const PAGE = 200

/** Rows arrive newest month first, so grouping is one pass, not a sort. */
function byMonth(rows: LibraryFile[]): Array<[string, LibraryFile[]]> {
  const groups: Array<[string, LibraryFile[]]> = []
  for (const row of rows) {
    const last = groups[groups.length - 1]
    if (last && last[0] === row.month) last[1].push(row)
    else groups.push([row.month || 'Unfiled', [row]])
  }
  return groups
}

export function LibraryPage() {
  const [filters, setFilters] = useState<LibraryFilters>({})
  const [rows, setRows] = useState<LibraryFile[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [tags, setTags] = useState<TagWithCount[]>([])
  const [selection, setSelection] = useState<Selection>(NO_SELECTION)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getFacets().then(setFacets).catch((e) => setError(String(e)))
    listTags().then(setTags).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // A selection made under one filter means nothing under the next.
    setSelection(clear())
    listLibraryFiles(filters, { limit: PAGE, offset: 0 })
      .then((result) => {
        if (cancelled) return
        setRows(result.rows)
        setTotal(result.total)
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [filters])

  const ordered = useMemo(() => rows.map((row) => row.drive_id), [rows])

  async function loadMore() {
    const result = await listLibraryFiles(filters, { limit: PAGE, offset: rows.length })
    setRows((current) => [...current, ...result.rows])
    setTotal(result.total)
  }

  async function onSelectAll() {
    try {
      setSelection(selectAll(await listLibraryIds(filters)))
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="library">
      <FilterSidebar
        facets={facets}
        tags={tags}
        filters={filters}
        onChange={setFilters}
      />

      <section className="grid-pane">
        <header className="grid-header">
          <h2>Library</h2>
          <p className="muted">
            {total} file{total === 1 ? '' : 's'}
            {rows.length < total ? ` — showing ${rows.length}` : ''}
          </p>
          {selection.ids.size > 0 && (
            <p className="selection-count">{selection.ids.size} selected</p>
          )}
          <button type="button" onClick={onSelectAll}>
            Select all matching this filter
          </button>
          {selection.ids.size > 0 && (
            <button type="button" onClick={() => setSelection(clear())}>
              Clear selection
            </button>
          )}
          {/* Task 14 mounts the bulk tag toolbar here. */}
        </header>

        {error && <p className="error">{error}</p>}
        {!loading && rows.length === 0 && (
          <p className="muted">No files match these filters.</p>
        )}

        {byMonth(rows).map(([month, files]) => (
          <section key={month}>
            <h3>{month}</h3>
            <div className="grid">
              {files.map((file) => (
                <div
                  key={file.drive_id}
                  className={isSelected(selection, file.drive_id) ? 'tile selected' : 'tile'}
                  aria-selected={isSelected(selection, file.drive_id)}
                  onClick={(event) =>
                    setSelection((current) =>
                      click(current, file.drive_id, ordered, {
                        shift: event.shiftKey,
                        meta: event.metaKey || event.ctrlKey,
                      }),
                    )
                  }
                >
                  <Thumb driveId={file.drive_id} name={file.name} />
                  <span className="tile-name">{file.name}</span>
                  {file.duplicate_of && <span className="badge">duplicate</span>}
                  {file.tags.map((tag) => (
                    <span key={tag.id} className="swatch" style={{ background: tag.color }} />
                  ))}
                </div>
              ))}
            </div>
          </section>
        ))}

        {rows.length < total && (
          <button type="button" onClick={loadMore}>
            Load more
          </button>
        )}
      </section>
      {/* Task 15 mounts the lightbox here. */}
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS, 12 `LibraryPage` tests.

- [ ] **Step 5: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/LibraryPage.tsx web/src/pages/LibraryPage.test.tsx
git commit -m "feat(web): Library page — month-grouped grid with filters and selection"
```

---

## Task 14: The bulk tag toolbar

**Files:**
- Create: `web/src/components/TagPicker.tsx`
- Modify: `web/src/pages/LibraryPage.tsx` (mount it at the marked seam; add two tests)
- Test: `web/src/components/TagPicker.test.tsx`, `web/src/pages/LibraryPage.test.tsx`

**Interfaces:**
- Consumes: `addFilesToTag`, `removeFilesFromTag`, `createTag` (Task 10).
- Produces:

```tsx
export function TagPicker(props: {
  tags: TagWithCount[]
  driveIds: string[]
  onApplied: () => void       // parent refetches tags and rows
  tagCount?: number           // tags on this one file; drives the ceiling warning
}): JSX.Element | null
```

Renders nothing when `driveIds` is empty. Creating a tag on the spot and applying it is one action, because "create then find it in the list then apply it" is three steps for something you decided in one.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/TagPicker.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TagPicker } from './TagPicker'

const addFilesToTag = vi.fn(async () => ({ added: 2 }))
const removeFilesFromTag = vi.fn(async () => ({ removed: 2 }))
const createTag = vi.fn(async (name: string) => ({
  id: 9, name, slug: name.toLowerCase(), color: '#000', file_count: 0,
}))

vi.mock('../api/client', () => ({
  addFilesToTag: (...args: unknown[]) => addFilesToTag(...(args as [])),
  removeFilesFromTag: (...args: unknown[]) => removeFilesFromTag(...(args as [])),
  createTag: (...args: unknown[]) => createTag(...(args as [])),
}))

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#f00', file_count: 3 },
  { id: 2, name: 'Print These', slug: 'print-these', color: '#0f0', file_count: 1 },
]

afterEach(() => vi.clearAllMocks())

describe('TagPicker', () => {
  it('renders nothing with no selection', () => {
    const { container } = render(
      <TagPicker tags={TAGS} driveIds={[]} onApplied={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('says how many files it will affect', () => {
    render(<TagPicker tags={TAGS} driveIds={['d1', 'd2']} onApplied={vi.fn()} />)
    expect(screen.getByText(/2 file/i)).toBeTruthy()
  })

  it('adds an existing tag to the selection', async () => {
    const onApplied = vi.fn()
    render(<TagPicker tags={TAGS} driveIds={['d1', 'd2']} onApplied={onApplied} />)

    await userEvent.selectOptions(screen.getByLabelText(/tag/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^add tag$/i }))

    expect(addFilesToTag).toHaveBeenCalledWith(1, ['d1', 'd2'])
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
  })

  it('removes a tag from the selection', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.selectOptions(screen.getByLabelText(/tag/i), '2')
    await userEvent.click(screen.getByRole('button', { name: /remove tag/i }))

    expect(removeFilesFromTag).toHaveBeenCalledWith(2, ['d1'])
  })

  it('creates a new tag and applies it in one action', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece 2025')
    await userEvent.click(screen.getByRole('button', { name: /create and apply/i }))

    await waitFor(() => expect(createTag).toHaveBeenCalledWith('Greece 2025'))
    await waitFor(() => expect(addFilesToTag).toHaveBeenCalledWith(9, ['d1']))
  })

  it('will not create a tag with a blank name', async () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /create and apply/i }))
    expect(createTag).not.toHaveBeenCalled()
  })

  it('surfaces a failure instead of pretending it worked', async () => {
    addFilesToTag.mockRejectedValueOnce(new Error('409: a tag named that exists'))
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} />)

    await userEvent.selectOptions(screen.getByLabelText(/tag/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^add tag$/i }))

    expect(await screen.findByText(/409/)).toBeTruthy()
  })

  it('warns before the appProperties ceiling rather than after', () => {
    render(<TagPicker tags={TAGS} driveIds={['d1']} onApplied={vi.fn()} tagCount={26} />)
    expect(screen.getByText(/25/)).toBeTruthy()
  })
})
```

Append to `web/src/pages/LibraryPage.test.tsx`:

```tsx
  it('offers bulk tagging once something is selected', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(screen.getByRole('button', { name: /^add tag$/i })).toBeTruthy()
  })

  it('offers no tagging controls with nothing selected', async () => {
    render(<LibraryPage />)
    await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(screen.queryByRole('button', { name: /^add tag$/i })).toBeNull()
  })
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./TagPicker`.

- [ ] **Step 3: Write `TagPicker.tsx`**

Create `web/src/components/TagPicker.tsx`:

```tsx
import { useState } from 'react'
import { addFilesToTag, createTag, removeFilesFromTag } from '../api/client'
import type { TagWithCount } from '../api/types'

// Drive allows 30 appProperties per file and Organize already writes about
// five. Warning here turns a later opaque API failure into a visible limit.
const MAX_TAGS = 25

export function TagPicker({
  tags,
  driveIds,
  onApplied,
  tagCount,
}: {
  tags: TagWithCount[]
  driveIds: string[]
  onApplied: () => void
  tagCount?: number
}) {
  const [tagId, setTagId] = useState<number | ''>('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (driveIds.length === 0) return null

  async function apply(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onApplied()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="tag-picker">
      <span>
        {driveIds.length} file{driveIds.length === 1 ? '' : 's'}
      </span>

      <label>
        Tag
        <select
          value={tagId}
          onChange={(event) => setTagId(event.target.value ? Number(event.target.value) : '')}
        >
          <option value="">Choose…</option>
          {tags.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {tag.name}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        disabled={busy || tagId === ''}
        onClick={() => apply(() => addFilesToTag(Number(tagId), driveIds))}
      >
        Add tag
      </button>
      <button
        type="button"
        disabled={busy || tagId === ''}
        onClick={() => apply(() => removeFilesFromTag(Number(tagId), driveIds))}
      >
        Remove tag
      </button>

      <label>
        New tag
        <input
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder="greece-2025"
        />
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          const name = newName.trim()
          if (!name) return
          return apply(async () => {
            const tag = await createTag(name)
            await addFilesToTag(tag.id, driveIds)
            setNewName('')
          })
        }}
      >
        Create and apply
      </button>

      {tagCount !== undefined && tagCount > MAX_TAGS && (
        <p className="warn">
          This file carries {tagCount} tags. Only {MAX_TAGS} fit in Drive's
          appProperties, so Sync Tags will skip it until you remove some.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
```

- [ ] **Step 4: Mount it in the Library page**

In `web/src/pages/LibraryPage.tsx`, import it and replace the `{/* Task 14 mounts the bulk tag toolbar here. */}` comment:

```tsx
import { TagPicker } from '../components/TagPicker'
```

```tsx
          <TagPicker
            tags={tags}
            driveIds={[...selection.ids]}
            onApplied={() => {
              listTags().then(setTags).catch((e) => setError(String(e)))
              listLibraryFiles(filters, { limit: rows.length || PAGE, offset: 0 })
                .then((result) => {
                  setRows(result.rows)
                  setTotal(result.total)
                })
                .catch((e) => setError(String(e)))
            }}
          />
```

Refetching rather than patching state locally is deliberate: the tag counts in the sidebar and the swatches on each tile both have to move, and re-reading is cheaper to get right than reconciling two derived views by hand.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS — 8 `TagPicker` tests, and `LibraryPage` now at 14.

- [ ] **Step 6: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/TagPicker.tsx web/src/components/TagPicker.test.tsx \
        web/src/pages/LibraryPage.tsx web/src/pages/LibraryPage.test.tsx
git commit -m "feat(web): bulk tag toolbar over the current selection"
```

---

## Task 15: The lightbox

**Files:**
- Create: `web/src/components/Lightbox.tsx`
- Modify: `web/src/pages/LibraryPage.tsx` (mount at the marked seam; add three tests)
- Test: `web/src/components/Lightbox.test.tsx`, `web/src/pages/LibraryPage.test.tsx`

**Interfaces:**
- Consumes: `getLibraryFile`, `thumbUrl` (Task 10); `Thumb` (Task 12); `TagPicker` (Task 14).
- Produces:

```tsx
export function Lightbox(props: {
  driveId: string
  tags: TagWithCount[]
  onClose: () => void
  onChanged: () => void
}): JSX.Element
```

Images render through the same proxy at `size=1600`. Videos are **not** rendered as `<video>`: 468 of these files are HEVC `.MOV`, which browsers refuse. They get Drive's embedded preview iframe at `https://drive.google.com/file/d/<id>/preview`, which is the only thing that plays them.

Opening the lightbox must not disturb the grid selection — you open a file to look at it, and losing a 300-file selection to a stray double-click would be maddening. The page opens it on double-click.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Lightbox.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Lightbox } from './Lightbox'

const IMAGE = {
  drive_id: 'd1', name: 'IMG_1.HEIC', month: '2025-06', mime_type: 'image/heic',
  media_type: 'image', size: 1048576, md5: 'abc', capture_time: 1750000000,
  capture_source: 'photo_taken_time', place: 'Warsaw', country: 'Poland',
  duplicate_of: null, duplicate_reason: null, archive_name: 'part-001.zip',
  tags: [{ id: 1, name: 'Family', slug: 'family', color: '#f00' }],
}

const VIDEO = {
  ...IMAGE, drive_id: 'd2', name: 'VID_1.MOV', media_type: 'video',
  mime_type: 'video/quicktime', place: null, country: null, tags: [],
  duplicate_of: '2025-06', duplicate_reason: 'name and size match an existing file',
}

const getLibraryFile = vi.fn(async (id: string) => (id === 'd2' ? VIDEO : IMAGE))

vi.mock('../api/client', () => ({
  getLibraryFile: (id: string) => getLibraryFile(id),
  thumbUrl: (id: string, size = 400) => `/api/thumb/${id}?size=${size}`,
  addFilesToTag: vi.fn(async () => ({ added: 1 })),
  removeFilesFromTag: vi.fn(async () => ({ removed: 1 })),
  createTag: vi.fn(async () => ({ id: 9, name: 'n', slug: 'n', color: '#0', file_count: 0 })),
}))

afterEach(() => vi.clearAllMocks())

const props = { tags: [], onClose: vi.fn(), onChanged: vi.fn() }

describe('Lightbox', () => {
  it('shows the file name', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByRole('heading', { name: 'IMG_1.HEIC' })).toBeTruthy()
  })

  it('renders an image at the large size', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    const image = await screen.findByRole('img', { name: 'IMG_1.HEIC' })
    expect(image.getAttribute('src')).toContain('size=1600')
  })

  it('plays a video in Drive’s own preview, which browsers cannot do natively', async () => {
    const { container } = render(<Lightbox driveId="d2" {...props} />)
    await screen.findByRole('heading', { name: 'VID_1.MOV' })
    const frame = container.querySelector('iframe')
    expect(frame?.getAttribute('src')).toBe('https://drive.google.com/file/d/d2/preview')
  })

  it('shows capture date, place, and source archive', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText(/Warsaw/)).toBeTruthy()
    expect(screen.getByText(/Poland/)).toBeTruthy()
    expect(screen.getByText(/part-001.zip/)).toBeTruthy()
  })

  it('says where a date came from, so a fallback is visible', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText(/photo_taken_time/)).toBeTruthy()
  })

  it('explains a duplicate flag', async () => {
    render(<Lightbox driveId="d2" {...props} />)
    expect(await screen.findByText(/name and size match/i)).toBeTruthy()
  })

  it('lists the tags on the file', async () => {
    render(<Lightbox driveId="d1" {...props} />)
    expect(await screen.findByText('Family')).toBeTruthy()
  })

  it('closes on the button', async () => {
    const onClose = vi.fn()
    render(<Lightbox driveId="d1" {...props} onClose={onClose} />)
    await userEvent.click(await screen.findByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    render(<Lightbox driveId="d1" {...props} onClose={onClose} />)
    await screen.findByRole('heading', { name: 'IMG_1.HEIC' })
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })
})
```

Append to `web/src/pages/LibraryPage.test.tsx`:

```tsx
  it('opens the lightbox on double-click', async () => {
    render(<LibraryPage />)
    await userEvent.dblClick(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(await screen.findByRole('dialog')).toBeTruthy()
  })

  it('does not open the lightbox on a single click', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('keeps the selection when the lightbox opens', async () => {
    render(<LibraryPage />)
    await userEvent.click(await screen.findByRole('img', { name: 'IMG_1.HEIC' }))
    await userEvent.click(screen.getByRole('img', { name: 'IMG_9.HEIC' }), { shiftKey: true })
    await userEvent.dblClick(screen.getByRole('img', { name: 'IMG_9.HEIC' }))
    expect(screen.getByText(/3 selected/i)).toBeTruthy()
  })
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./Lightbox`.

- [ ] **Step 3: Write `Lightbox.tsx`**

Create `web/src/components/Lightbox.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getLibraryFile, thumbUrl } from '../api/client'
import type { LibraryFile, TagWithCount } from '../api/types'
import { TagPicker } from './TagPicker'

function when(seconds: number | null): string {
  if (seconds === null) return 'unknown'
  return new Date(seconds * 1000).toISOString().replace('T', ' ').slice(0, 16)
}

function megabytes(bytes: number | null): string {
  return bytes === null ? '—' : `${(bytes / 1e6).toFixed(1)} MB`
}

export function Lightbox({
  driveId,
  tags,
  onClose,
  onChanged,
}: {
  driveId: string
  tags: TagWithCount[]
  onClose: () => void
  onChanged: () => void
}) {
  const [file, setFile] = useState<LibraryFile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getLibraryFile(driveId).then(setFile).catch((e) => setError(String(e)))
  }, [driveId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="lightbox" role="dialog" aria-modal="true">
      <button type="button" className="close" onClick={onClose}>
        Close
      </button>
      {error && <p className="error">{error}</p>}
      {file && (
        <>
          <h2>{file.name}</h2>

          {file.media_type === 'video' ? (
            // 468 of these are HEVC .MOV, which no browser plays. Drive's own
            // player does, so the lightbox embeds it rather than pretending.
            <iframe
              title={file.name}
              src={`https://drive.google.com/file/d/${file.drive_id}/preview`}
              allow="autoplay"
            />
          ) : (
            <img src={thumbUrl(file.drive_id, 1600)} alt={file.name} />
          )}

          <dl className="meta">
            <dt>Taken</dt>
            <dd>
              {when(file.capture_time)}
              {file.capture_source && <span className="muted"> ({file.capture_source})</span>}
            </dd>
            <dt>Month</dt>
            <dd>{file.month || 'Unfiled'}</dd>
            <dt>Place</dt>
            <dd>
              {file.place ?? '—'}
              {file.country ? `, ${file.country}` : ''}
            </dd>
            <dt>Size</dt>
            <dd>{megabytes(file.size)}</dd>
            <dt>From archive</dt>
            <dd>{file.archive_name ?? 'not from an archive'}</dd>
          </dl>

          {file.duplicate_of && (
            <p className="warn">
              Flagged as a duplicate of something in {file.duplicate_of}:{' '}
              {file.duplicate_reason}. It was uploaded anyway.
            </p>
          )}

          <div className="tag-list">
            {file.tags.length === 0 && <span className="muted">No tags</span>}
            {file.tags.map((tag) => (
              <span key={tag.id} className="chip" style={{ borderColor: tag.color }}>
                {tag.name}
              </span>
            ))}
          </div>

          <TagPicker
            tags={tags}
            driveIds={[file.drive_id]}
            tagCount={file.tags.length}
            onApplied={() => {
              getLibraryFile(driveId).then(setFile).catch((e) => setError(String(e)))
              onChanged()
            }}
          />
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Mount it in the Library page**

In `web/src/pages/LibraryPage.tsx`, add the import, a state hook, a double-click handler on the tile, and replace the `{/* Task 15 mounts the lightbox here. */}` comment.

```tsx
import { Lightbox } from '../components/Lightbox'
```

```tsx
  const [openFile, setOpenFile] = useState<string | null>(null)
```

On the tile `div`, alongside `onClick`:

```tsx
                  onDoubleClick={() => setOpenFile(file.drive_id)}
```

And at the seam:

```tsx
      {openFile && (
        <Lightbox
          driveId={openFile}
          tags={tags}
          onClose={() => setOpenFile(null)}
          onChanged={() => {
            listTags().then(setTags).catch((e) => setError(String(e)))
            listLibraryFiles(filters, { limit: rows.length || PAGE, offset: 0 })
              .then((result) => {
                setRows(result.rows)
                setTotal(result.total)
              })
              .catch((e) => setError(String(e)))
          }}
        />
      )}
```

Opening on double-click rather than single is what keeps a 300-file selection intact while you inspect one of them.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS — 9 `Lightbox` tests, `LibraryPage` now at 17.

- [ ] **Step 6: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/components/Lightbox.tsx web/src/components/Lightbox.test.tsx \
        web/src/pages/LibraryPage.tsx web/src/pages/LibraryPage.test.tsx
git commit -m "feat(web): lightbox with metadata, per-file tagging, and Drive video preview"
```

---

## Task 16: The Tags page

**Files:**
- Create: `web/src/pages/TagsPage.tsx`
- Test: `web/src/pages/TagsPage.test.tsx`

**Interfaces:**
- Consumes: `listTags`, `createTag`, `patchTag`, `deleteTag`, `mergeTags` (Task 10).
- Produces: `export function TagsPage(): JSX.Element`.

Delete asks for confirmation inline — a second click on a button that has turned into "Really delete?" — rather than a `window.confirm`, which is untestable and blocks the event loop. Deleting a tag removes the assignment, never the file; the page says so, because that is the question anyone hesitates over.

- [ ] **Step 1: Write the failing test**

Create `web/src/pages/TagsPage.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TagsPage } from './TagsPage'

const TAGS = [
  { id: 1, name: 'Family', slug: 'family', color: '#ff0000', file_count: 12 },
  { id: 2, name: 'Familly', slug: 'familly', color: '#00ff00', file_count: 3 },
]

const listTags = vi.fn(async () => TAGS)
const createTag = vi.fn(async (name: string) => ({
  id: 3, name, slug: 'x', color: '#000', file_count: 0,
}))
const patchTag = vi.fn(async () => TAGS[0])
const deleteTag = vi.fn(async () => ({ deleted: 2 }))
const mergeTags = vi.fn(async () => ({ moved: 3, target: TAGS[0] }))

vi.mock('../api/client', () => ({
  listTags: () => listTags(),
  createTag: (...args: unknown[]) => createTag(...(args as [string])),
  patchTag: (...args: unknown[]) => patchTag(...(args as [])),
  deleteTag: (...args: unknown[]) => deleteTag(...(args as [])),
  mergeTags: (...args: unknown[]) => mergeTags(...(args as [])),
}))

afterEach(() => vi.clearAllMocks())

const row = (name: string) => screen.getByText(name).closest('tr') as HTMLElement

describe('TagsPage', () => {
  it('lists every tag with its file count', async () => {
    render(<TagsPage />)
    await screen.findByText('Family')
    expect(row('Family').textContent).toContain('12')
  })

  it('creates a tag', async () => {
    render(<TagsPage />)
    await screen.findByText('Family')
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece 2025')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => expect(createTag).toHaveBeenCalledWith('Greece 2025'))
  })

  it('reloads after creating, so the count is real', async () => {
    render(<TagsPage />)
    await screen.findByText('Family')
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Greece')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    await waitFor(() => expect(listTags).toHaveBeenCalledTimes(2))
  })

  it('renames a tag', async () => {
    render(<TagsPage />)
    await screen.findByText('Familly')
    const input = within(row('Familly')).getByLabelText(/name/i)
    await userEvent.clear(input)
    await userEvent.type(input, 'Friends')
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /save/i }))
    await waitFor(() => expect(patchTag).toHaveBeenCalledWith(2, { name: 'Friends' }))
  })

  it('recolours a tag', async () => {
    render(<TagsPage />)
    await screen.findByText('Family')
    const picker = within(row('Family')).getByLabelText(/colou?r/i)
    await userEvent.clear(picker)
    await userEvent.type(picker, '#0000ff')
    await userEvent.click(within(row('Family')).getByRole('button', { name: /save/i }))
    await waitFor(() =>
      expect(patchTag).toHaveBeenCalledWith(1, expect.objectContaining({ color: '#0000ff' })),
    )
  })

  it('asks before deleting', async () => {
    render(<TagsPage />)
    await screen.findByText('Familly')
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /^delete$/i }))
    expect(deleteTag).not.toHaveBeenCalled()
    expect(within(row('Familly')).getByRole('button', { name: /really/i })).toBeTruthy()
  })

  it('deletes on the second click', async () => {
    render(<TagsPage />)
    await screen.findByText('Familly')
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /^delete$/i }))
    await userEvent.click(within(row('Familly')).getByRole('button', { name: /really/i }))
    await waitFor(() => expect(deleteTag).toHaveBeenCalledWith(2))
  })

  it('says that deleting a tag keeps the files', async () => {
    render(<TagsPage />)
    await screen.findByText('Family')
    expect(screen.getByText(/no files are deleted/i)).toBeTruthy()
  })

  it('merges one tag into another', async () => {
    render(<TagsPage />)
    await screen.findByText('Familly')
    await userEvent.selectOptions(screen.getByLabelText(/merge/i), '2')
    await userEvent.selectOptions(screen.getByLabelText(/into/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^merge$/i }))
    await waitFor(() => expect(mergeTags).toHaveBeenCalledWith(2, 1))
  })

  it('refuses to merge a tag into itself', async () => {
    render(<TagsPage />)
    await screen.findByText('Familly')
    await userEvent.selectOptions(screen.getByLabelText(/merge/i), '1')
    await userEvent.selectOptions(screen.getByLabelText(/into/i), '1')
    await userEvent.click(screen.getByRole('button', { name: /^merge$/i }))
    expect(mergeTags).not.toHaveBeenCalled()
  })

  it('surfaces a duplicate-name failure', async () => {
    createTag.mockRejectedValueOnce(new Error('409: a tag named that exists'))
    render(<TagsPage />)
    await screen.findByText('Family')
    await userEvent.type(screen.getByLabelText(/new tag/i), 'Family')
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    expect(await screen.findByText(/409/)).toBeTruthy()
  })

  it('points at the Library when there are no tags yet', async () => {
    listTags.mockResolvedValueOnce([])
    render(<TagsPage />)
    expect(await screen.findByText(/no tags yet/i)).toBeTruthy()
  })

  it('reminds you that Drive learns about tags only on sync', async () => {
    render(<TagsPage />)
    expect(await screen.findByText(/sync tags/i)).toBeTruthy()
  })
})
```

Add `within` to the `@testing-library/react` import at the top of the file.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./TagsPage`.

- [ ] **Step 3: Write the page**

Create `web/src/pages/TagsPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { createTag, deleteTag, listTags, mergeTags, patchTag } from '../api/client'
import type { TagWithCount } from '../api/types'

function TagRow({
  tag,
  onSaved,
  onDeleted,
}: {
  tag: TagWithCount
  onSaved: (id: number, patch: { name?: string; color?: string }) => void
  onDeleted: (id: number) => void
}) {
  const [name, setName] = useState(tag.name)
  const [color, setColor] = useState(tag.color)
  const [confirming, setConfirming] = useState(false)

  return (
    <tr>
      <td>
        <span className="swatch" style={{ background: tag.color }} />
        {tag.name}
      </td>
      <td>
        <label>
          Name
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
      </td>
      <td>
        <label>
          Colour
          <input value={color} onChange={(event) => setColor(event.target.value)} />
        </label>
      </td>
      <td>{tag.file_count}</td>
      <td>
        <button
          type="button"
          onClick={() => {
            const patch: { name?: string; color?: string } = {}
            if (name !== tag.name) patch.name = name
            if (color !== tag.color) patch.color = color
            if (Object.keys(patch).length > 0) onSaved(tag.id, patch)
          }}
        >
          Save
        </button>
        {confirming ? (
          <button type="button" className="danger" onClick={() => onDeleted(tag.id)}>
            Really delete?
          </button>
        ) : (
          // An inline second click rather than window.confirm, which blocks
          // the event loop and cannot be tested.
          <button type="button" onClick={() => setConfirming(true)}>
            Delete
          </button>
        )}
      </td>
    </tr>
  )
}

export function TagsPage() {
  const [tags, setTags] = useState<TagWithCount[]>([])
  const [newName, setNewName] = useState('')
  const [source, setSource] = useState<number | ''>('')
  const [target, setTarget] = useState<number | ''>('')
  const [error, setError] = useState<string | null>(null)

  function reload() {
    listTags().then(setTags).catch((e) => setError(String(e)))
  }

  useEffect(reload, [])

  async function guard(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      reload()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <>
      <h2>Tags</h2>
      <p className="muted">
        Tags live in the catalog. Run <strong>Sync Tags to Drive</strong> to
        mirror them onto the files themselves.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="card">
        <label>
          New tag
          <input value={newName} onChange={(event) => setNewName(event.target.value)} />
        </label>
        <button
          type="button"
          onClick={() => {
            const name = newName.trim()
            if (!name) return
            return guard(async () => {
              await createTag(name)
              setNewName('')
            })
          }}
        >
          Create
        </button>
      </div>

      {tags.length === 0 ? (
        <p className="muted">
          No tags yet. Select some files on the Library page and create one there.
        </p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Tag</th>
                <th>Name</th>
                <th>Colour</th>
                <th>Files</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tags.map((tag) => (
                <TagRow
                  key={tag.id}
                  tag={tag}
                  onSaved={(id, patch) => guard(() => patchTag(id, patch))}
                  onDeleted={(id) => guard(() => deleteTag(id))}
                />
              ))}
            </tbody>
          </table>
          <p className="muted">
            Deleting a tag removes it from every file. No files are deleted.
          </p>

          <div className="card">
            <label>
              Merge
              <select
                value={source}
                onChange={(event) => setSource(event.target.value ? Number(event.target.value) : '')}
              >
                <option value="">Choose…</option>
                {tags.map((tag) => (
                  <option key={tag.id} value={tag.id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              into
              <select
                value={target}
                onChange={(event) => setTarget(event.target.value ? Number(event.target.value) : '')}
              >
                <option value="">Choose…</option>
                {tags.map((tag) => (
                  <option key={tag.id} value={tag.id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => {
                if (source === '' || target === '' || source === target) return
                return guard(async () => {
                  await mergeTags(Number(source), Number(target))
                  setSource('')
                  setTarget('')
                })
              }}
            >
              Merge
            </button>
          </div>
        </>
      )}
    </>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: PASS, 13 `TagsPage` tests.

- [ ] **Step 5: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/TagsPage.tsx web/src/pages/TagsPage.test.tsx
git commit -m "feat(web): Tags page — create, rename, recolour, merge, delete"
```

---

## Task 17: Routing, navigation, and styles

**Files:**
- Modify: `web/src/App.tsx:1-34`
- Modify: `web/src/components/Nav.tsx:17-25`
- Modify: `web/src/styles.css` (append)
- Test: `web/src/components/Nav.test.tsx` (create)

**Interfaces:**
- Consumes: `LibraryPage` (Task 13), `TagsPage` (Task 16).
- Produces: routes `/library` and `/tags`; nav links under a new "Browse" section.

The Library grid needs the full window, but `styles.css:20` caps `main` at `60rem`. Rather than restructure the layout, widen `main` only when it contains the library.

- [ ] **Step 1: Write the failing test**

Create `web/src/components/Nav.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Nav } from './Nav'

const ACTIONS = [
  { id: 'scan_archives', title: 'Scan Archives', description: '', order: 10, schema: { type: 'object' } },
]

describe('Nav', () => {
  it('links to the Library and Tags pages', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /library/i }).getAttribute('href')).toBe('/library')
    expect(screen.getByRole('link', { name: /tags/i }).getAttribute('href')).toBe('/tags')
  })

  it('still lists the actions it is given', () => {
    render(
      <MemoryRouter>
        <Nav actions={ACTIONS} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Scan Archives' })).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — no link named "Library".

- [ ] **Step 3: Add the nav links**

In `web/src/components/Nav.tsx`, add a section before "Activity":

```tsx
      <section>
        <h2>Browse</h2>
        <NavLink to="/library">Library</NavLink>
        <NavLink to="/tags">Tags</NavLink>
      </section>
```

- [ ] **Step 4: Add the routes**

In `web/src/App.tsx`, import both pages and add their routes:

```tsx
import { LibraryPage } from './pages/LibraryPage'
import { TagsPage } from './pages/TagsPage'
```

```tsx
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/tags" element={<TagsPage />} />
```

- [ ] **Step 5: Add the styles**

Append to `web/src/styles.css`:

```css
/* The grid wants the whole window; every other page is happier narrow. */
main:has(.library) { max-width: none; }

.library { display: grid; grid-template-columns: 15rem 1fr; gap: 1.5rem; }

.filters h3 { font-size: 0.75rem; text-transform: uppercase; opacity: 0.6; margin: 1rem 0 0.3rem; }
.filters label { display: block; margin-bottom: 0.75rem; font-size: 0.8rem; }
.filters input[type='search'] { width: 100%; }

.facet-item {
  display: flex; align-items: center; gap: 0.4rem; width: 100%;
  padding: 0.25rem 0.4rem; text-align: left; background: none;
  border: 1px solid transparent; border-radius: 4px; cursor: pointer;
  color: inherit; font: inherit;
}
.facet-item:hover { background: rgba(128, 128, 128, 0.12); }
.facet-item.active { border-color: currentColor; font-weight: 600; }
.facet-item .count { margin-left: auto; opacity: 0.55; font-variant-numeric: tabular-nums; }

.grid-header { display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
.selection-count { font-weight: 600; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
  gap: 0.6rem;
}

.tile {
  position: relative; cursor: pointer; border-radius: 6px;
  border: 2px solid transparent; padding: 0.25rem; overflow: hidden;
  user-select: none;
}
.tile.selected { border-color: Highlight; background: rgba(128, 128, 128, 0.15); }
.tile-name { display: block; font-size: 0.7rem; opacity: 0.7; word-break: break-all; }

.thumb { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 4px; display: block; }
.thumb-pending, .thumb-missing {
  display: grid; place-items: center; font-size: 0.75rem;
  background: rgba(128, 128, 128, 0.15); opacity: 0.7;
}

.badge {
  position: absolute; top: 0.4rem; right: 0.4rem;
  background: #b8860b; color: white; font-size: 0.6rem;
  padding: 0.1rem 0.3rem; border-radius: 3px;
}

.swatch {
  display: inline-block; width: 0.6rem; height: 0.6rem;
  border-radius: 50%; margin-right: 0.2rem;
}

.chip { border: 1px solid; border-radius: 999px; padding: 0.1rem 0.5rem; font-size: 0.75rem; margin-right: 0.3rem; }

.tag-picker {
  display: flex; align-items: end; gap: 0.6rem; flex-wrap: wrap;
  padding: 0.6rem; border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 8px;
}
.tag-picker label { display: flex; flex-direction: column; font-size: 0.75rem; }

.lightbox {
  position: fixed; inset: 0; background: Canvas; padding: 1.5rem;
  overflow-y: auto; z-index: 10;
}
.lightbox img, .lightbox iframe {
  max-width: 100%; max-height: 65vh; display: block; margin: 0 auto;
  border: 0; width: 100%; aspect-ratio: 16 / 10;
}
.lightbox img { width: auto; aspect-ratio: auto; }
.lightbox .close { position: absolute; top: 1rem; right: 1.5rem; }
.lightbox .meta { display: grid; grid-template-columns: 8rem 1fr; gap: 0.2rem 1rem; }
.lightbox dt { font-size: 0.75rem; text-transform: uppercase; opacity: 0.6; }

.danger { color: #c0392b; font-weight: 600; }
.link { background: none; border: 0; color: inherit; text-decoration: underline; cursor: pointer; padding: 0; }
```

- [ ] **Step 6: Run the whole frontend suite**

Run: `cd web && npm test`
Expected: PASS, every test file.

- [ ] **Step 7: Typecheck and lint**

Run: `cd web && npm run build && npm run lint`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add web/src/App.tsx web/src/components/Nav.tsx web/src/components/Nav.test.tsx web/src/styles.css
git commit -m "feat(web): route and style the Library and Tags pages"
```

---

## Task 18: Live test and documentation

**Files:**
- Create: `tests/test_live_phase4.py`
- Modify: `README.md:40-61, 88-102`

**Interfaces:**
- Consumes: everything above.
- Produces: an opt-in live test, and a README that describes the pipeline as it now stands.

The live test is deselected by default (`addopts = "-m 'not live'"` in `pyproject.toml:44`). It proves the two things a fake cannot: that Drive really returns a thumbnail for a real file, and that an appProperties round-trip — write, read back, delete — behaves as `sync_tags` assumes.

- [ ] **Step 1: Write the live test**

Create `tests/test_live_phase4.py`:

```python
"""Opt-in checks against the real Drive account: `uv run pytest -m live`.

Everything else in the suite runs against a fake. These two facts cannot be
faked honestly: that Drive renders a thumbnail for a file we uploaded, and
that setting an appProperty to null really deletes it — which is the whole
mechanism by which `sync_tags` removes a tag.

Paths resolve relative to the repo root. From a git worktree the credentials
live in the main checkout, so point at it:
`PHOTOLIB_HOME=/path/to/main/checkout uv run pytest -m live`.
"""

from __future__ import annotations

import pytest

from photolib.config import Config
from photolib.db import catalog
from photolib.db.settings_repo import PHOTOS_ROOT, SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient
from photolib.drive.writer import DriveWriter

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def drive():
    config = Config.load()
    if not config.credentials_path.exists():
        pytest.skip("no credentials.json; set PHOTOLIB_HOME to the main checkout")
    client = DriveClient(TokenProvider(config.credentials_path, config.token_path))
    yield client
    client.close()


@pytest.fixture(scope="module")
def a_real_photo(drive):
    """One file already in the destination. Skips if nothing is organised yet."""
    config = Config.load()
    conn = catalog.connect(config.db_path)
    photos_root = SettingsRepo(conn).get_folder(PHOTOS_ROOT)
    if photos_root is None:
        pytest.skip("photos_root is not configured")
    row = conn.execute(
        "SELECT drive_id FROM drive_files WHERE trashed_at IS NULL "
        "AND mime_type LIKE 'image/%' LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        pytest.skip("no organised images yet; run Scan and Organize first")
    return row["drive_id"]


def test_drive_renders_a_thumbnail_for_a_real_file(drive, a_real_photo):
    content = drive.fetch_thumbnail(a_real_photo, 400)
    assert content is not None, "Drive returned no thumbnailLink"
    # JPEG magic. If this is HTML we followed an error page, not an image.
    assert content[:2] == b"\xff\xd8"


def test_the_two_sizes_really_differ(drive, a_real_photo):
    small = drive.fetch_thumbnail(a_real_photo, 400)
    large = drive.fetch_thumbnail(a_real_photo, 1600)
    assert small is not None and large is not None
    assert len(large) > len(small)


def test_an_app_property_round_trips_and_can_be_deleted(drive, a_real_photo):
    """The mechanism sync_tags relies on to remove a tag."""
    writer = DriveWriter(drive)
    before = drive.app_properties(a_real_photo)
    assert "t_photolib_live_test" not in before

    writer.update_properties(a_real_photo, {"t_photolib_live_test": "1"})
    try:
        assert drive.app_properties(a_real_photo)["t_photolib_live_test"] == "1"
    finally:
        writer.update_properties(a_real_photo, {"t_photolib_live_test": None})

    after = drive.app_properties(a_real_photo)
    assert "t_photolib_live_test" not in after
    # Nothing Organize wrote may have been disturbed.
    assert {k: v for k, v in after.items() if not k.startswith("t_")} == {
        k: v for k, v in before.items() if not k.startswith("t_")
    }
```

- [ ] **Step 2: Confirm it is deselected by default**

Run: `uv run pytest --collect-only -q | tail -5`
Expected: the count is unchanged from before this file existed, and no `test_live_phase4` entries appear.

- [ ] **Step 3: Run it against the real account (optional but recommended)**

Run: `uv run pytest -m live tests/test_live_phase4.py -v`
Expected: PASS, or SKIP with a clear reason if the pipeline has not been run yet. A skip here is an acceptable outcome; a failure is not.

- [ ] **Step 4: Update the README**

In the pipeline table, add the two new rows and mark which mutate Drive:

```markdown
| Action | Does | Writes to Drive |
| --- | --- | --- |
| Check Connection | Verifies credentials and folders | No |
| Scan Archives | Indexes archive contents and the destination folder | No |
| Pair Metadata | Matches sidecars to media across archive parts | No |
| Plan Organization | Resolves dates, places, duplicates, destinations | No |
| Review Plan | Shows every file and where it would go | No |
| **Organize Photos** | **Uploads every planned file into `Photos/YYYY-MM/`** | **Yes** |
| Library | Browse, filter, and tag what is now in Drive | No |
| Tags | Create, rename, merge, and delete tags | No |
| **Sync Tags to Drive** | **Mirrors tags onto each file's `appProperties`** | **Yes** |
| **Clear Stale Trees** | **Moves a redundant extracted tree to Drive's trash** | **Yes** |
```

Then add a section after "Clearing the stale trees":

```markdown
## Browsing and tagging

The Library page shows what Drive actually holds under `photos_root`, grouped
by month. It is built from the last Scan, so re-run Scan after an Organize to
see new files. Thumbnails come from Drive's own renderer through a local disk
cache in `.cache/thumbnails` — Chrome cannot display HEIC, and 591 of these
files are HEIC. Videos play in Drive's embedded preview, which handles the
HEVC `.MOV` files browsers refuse.

Filter by month, place, country, media type, tag, or duplicate status, and
combine them freely. Duplicates are a filter here, not a separate page: those
files were uploaded anyway, so they are part of the library. Select with
click, shift-click for a range, ⌘-click to toggle, or **Select all matching
this filter**, which selects the entire result set rather than only the tiles
on screen.

Tags are yours to invent — `family`, `greece-2025`, `print-these`. Place,
month, year and media type are *not* tags: they are filters derived from data
the catalog already holds, so there is nothing to regenerate and nothing to
go stale. Tagging writes only to the local catalog, which is why it is
instant.

**Sync Tags to Drive** is what makes tags durable. It compares each file's
`t_*` appProperties against the catalog, reports every add and removal, and
changes nothing until you re-run it with `confirm`. Tags then travel with the
file and survive the loss of this machine. Drive allows 30 properties per
file and Organize already uses about five, so a file carrying more than 25
tags is reported and skipped rather than failing obscurely.
```

Finally, extend the architecture list:

```markdown
- `photolib/thumbs.py` — disk-cached proxy for Drive's thumbnail renders
```

- [ ] **Step 5: Verify the whole suite, both sides**

Run: `uv run pytest && cd web && npm test && npm run build && npm run lint`
Expected: PASS throughout, no type errors, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live_phase4.py README.md
git commit -m "docs: describe browsing, tagging, and tag sync; add live Phase 4 checks"
```

---

## Done

At this point:

- The Library page browses everything in `photos_root`, grouped by month, with thumbnails Drive renders and this app caches.
- Filters compose across month, place, country, type, tag, duplicate status, and name search — all derived from columns, none of them stored as tags.
- Selection works the way a file manager does, including select-all-matching across the whole filtered set rather than the rendered page.
- Tags are created, applied in bulk, renamed, merged, and deleted, entirely in SQLite.
- `Sync Tags to Drive` mirrors them onto `appProperties`, reporting before it acts, and removing what you untagged.
- A re-Scan can no longer destroy a tag.

**Verification before calling it done** — run each and confirm the output rather than assuming:

```bash
uv run pytest                  # backend, offline
cd web && npm test             # frontend
cd web && npm run build        # typecheck
cd web && npm run lint
uv run pytest -m live          # optional, hits the real account
```

Then follow `superpowers:finishing-a-development-branch` to integrate.

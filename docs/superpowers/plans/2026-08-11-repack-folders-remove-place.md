# Repack Folders & Remove Place — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize every file in the Global Photos folder into ~100-file, whole-month "bucket" folders (including the uncatalogued legacy `back_*` files, dated from Drive metadata), and remove the Place filter/tag entirely, keeping Country.

**Architecture:** A pure packing module (`photolib/buckets.py`) turns a month→count histogram into named buckets; Plan Organization and a new report-then-confirm Reorganize action both consume it, so future uploads and existing files agree on destinations. Reorganize moves files with metadata-only `files.update` calls (new `DriveWriter.move`), strips the retired `place` appProperty, and trashes emptied folders. Place is deleted end to end: geocoder, DB column (migration), planner, upload properties, library API, and web UI.

**Tech Stack:** Python 3 + FastAPI + SQLite (run tests with `uv run pytest`), httpx against Drive API v3, React/TypeScript web UI (`cd web && npm test`, vitest).

**Spec:** `docs/superpowers/specs/2026-08-11-repack-folders-remove-place-design.md`

## Global Constraints

- Bucket packing: whole months only, greedy in chronological order; close a bucket when adding the next month would push it past **130** files; a single month over 130 stands alone. Target is ~100.
- Folder names: single-month bucket → `2026-05`; range bucket → `2025-01 - 2025-03` (first month, space-hyphen-space, last month); undated files → `unknown-date`.
- Capture precedence for bucketing: `media.capture_time` (catalogued) → `drive_files.capture_hint` (EXIF via Drive, else modifiedTime) → `unknown-date`.
- Reorganize is report-only unless `confirm` is set, mirroring `sync_tags` / `clear_stale_trees`.
- Nothing is permanently deleted: folders are trashed via `writer.trash`.
- Country stays everywhere Place is removed.
- Follow existing code style: module docstrings explaining "why", `ProgressEvent` reporting, tests named `test_<behavior_sentence>`.
- Python tests: `uv run pytest tests/ -x -q` (the `test_live_*.py` files self-skip without credentials). Web: `cd web && npm test`.

---

### Task 1: Commit the pending working-tree feature

The tree already carries a finished, unrelated improvement (deep destination walk in Scan Archives + trashed-copy duplicate fixes, with tests). It must land as its own commit so later commits stay reviewable.

**Files:**
- Commit as-is (no edits): `photolib/actions/plan_organize.py`, `photolib/actions/scan_archives.py`, `photolib/db/scan_repo.py`, `tests/test_action_plan.py`, `tests/test_action_scan.py`

- [ ] **Step 1: Verify the suite passes with the pending changes**

Run: `uv run pytest tests/ -x -q`
Expected: all pass (live tests skip).

- [ ] **Step 2: Commit**

```bash
git add photolib/actions/plan_organize.py photolib/actions/scan_archives.py \
        photolib/db/scan_repo.py tests/test_action_plan.py tests/test_action_scan.py
git commit -m "fix: walk the destination at any depth; trashed copies are not duplicates"
```

---

### Task 2: Remove Place from the backend

One atomic task because the pieces are load-bearing on each other: dropping `media.place` breaks every SQL string that names the column, so the migration, repos, planner, uploader, geocoder, and API routes must change together.

**Files:**
- Modify: `photolib/db/schema.sql` (media table, ~line 79)
- Modify: `photolib/db/migrations.py`
- Modify: `photolib/places.py`
- Modify: `photolib/db/media_repo.py` (`_PLAN_FIELDS`, `_UPLOAD_SELECT`, `summary`)
- Modify: `photolib/actions/plan_organize.py` (geocoder use, `set_plan`, DESCRIPTION)
- Modify: `photolib/actions/organize.py` (`_properties`)
- Modify: `photolib/db/library_repo.py` (`ROW_FIELDS`, `_SELECT`, `Filters`, `_where`, `facets`, docstring)
- Modify: `photolib/api/routes_library.py` (`_filters`)
- Modify: `photolib/api/routes_review.py` (`ROW_FIELDS`)
- Test: `tests/test_migrations.py`, `tests/test_places.py`, `tests/test_media_repo.py`, `tests/test_library_repo.py`, `tests/test_api_review.py`, `tests/test_action_plan.py`, `tests/test_action_organize.py` (if it asserts a `place` property)

**Interfaces:**
- Consumes: current schema v4.
- Produces: `Geocoder.lookup(lat, lon) -> str | None` (the country); `media` table without `place`; `Filters` without `place`; facets without `"places"`; `SCHEMA_VERSION = 5` and a `_DROPPED_COLUMNS` mechanism in `migrations.py`. Later tasks rely on all of these.

- [ ] **Step 1: Write the failing migration test**

Append to `tests/test_migrations.py` (reuse its existing fixture/helper style for building a connection if one exists; otherwise this standalone form works):

```python
def test_an_upgraded_catalog_loses_the_place_column(conn):
    from photolib.db.migrations import migrate

    conn.execute("ALTER TABLE media ADD COLUMN place TEXT")
    migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    assert "place" not in columns


def test_migrate_is_idempotent_about_dropped_columns(conn):
    from photolib.db.migrations import migrate

    migrate(conn)
    migrate(conn)   # a second run must not fail on the already-missing column
```

(The `conn` fixture in `tests/conftest.py` already runs `catalog.connect`, which migrates — so `media` in the first test gains `place` and the explicit `migrate` must drop it again.)

- [ ] **Step 2: Run the migration tests to verify they fail**

Run: `uv run pytest tests/test_migrations.py -x -q`
Expected: FAIL — `place` survives because nothing drops it yet.

- [ ] **Step 3: Implement the migration**

In `photolib/db/schema.sql`, delete the line `    place            TEXT,` from `CREATE TABLE IF NOT EXISTS media`.

In `photolib/db/migrations.py`, set `SCHEMA_VERSION = 5` and add below `_ADDED_COLUMNS`:

```python
# (table, column) pairs retired from the schema. SQLite 3.35+ supports
# DROP COLUMN, and nothing indexes or references these.
_DROPPED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("media", "place"),
)
```

and in `migrate`, after the `_ADDED_COLUMNS` loop:

```python
    for table, column in _DROPPED_COLUMNS:
        if column in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
```

- [ ] **Step 4: Run the migration tests to verify they pass**

Run: `uv run pytest tests/test_migrations.py -x -q`
Expected: PASS (including the existing fresh-vs-upgraded identity test).

- [ ] **Step 5: Make the geocoder country-only**

In `photolib/places.py`: delete the `PLACE_TYPES` constant; update the module docstring's first line to say "Reverse-geocoding to a country, with a persistent cache."; replace `lookup`, `_extract`, and `_store` with:

```python
    def lookup(self, lat: float, lon: float) -> str | None:
        """The country at these coordinates, or None."""
        key = cache_key(lat, lon)
        cached = self._conn.execute(
            "SELECT country FROM geocache WHERE key = ?", (key,)
        ).fetchone()
        if cached is not None:
            return cached["country"]

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
        self._store(key, country, payload)
        return country

    @staticmethod
    def _extract(payload: dict) -> str | None:
        for result in payload.get("results", []):
            for component in result.get("address_components", []):
                if "country" in component.get("types", []):
                    return component.get("long_name")
        return None

    def _store(self, key: str, country: str | None, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO geocache (key, country, raw_json) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET country = excluded.country, "
            "raw_json = excluded.raw_json",
            (key, country, json.dumps(payload)),
        )
        self._conn.commit()
```

The `geocache` table keeps its `place` column — it is a cache, old rows are harmless, and `raw_json` preserves everything.

- [ ] **Step 6: Update `tests/test_places.py`**

Mechanical rewrite against the new signature: every `place, country = geocoder.lookup(...)` unpack becomes `country = geocoder.lookup(...)`; delete assertions about `place`; rename `test_lookup_returns_place_and_country` to `test_lookup_returns_the_country`. Any fixture payloads keep their `address_components` — only the `locality` expectations go.

Run: `uv run pytest tests/test_places.py -x -q` — Expected: PASS.

- [ ] **Step 7: Remove place from the catalog write path**

`photolib/db/media_repo.py`:
- `_PLAN_FIELDS`: delete `"place",`.
- `_UPLOAD_SELECT`: change `m.capture_time, m.place, m.country,` to `m.capture_time, m.country,`.
- `summary()`: delete the `"with_place"` entry.

`photolib/actions/plan_organize.py`:
- DESCRIPTION: `"Resolve a capture date, country, duplicate verdict and destination for every media file. ..."`.
- The no-API-key warning becomes `"No GOOGLE_MAPS_API_KEY configured — country tags will be skipped."`.
- Rename the `placed` counter to `located`; in the loop replace the place block with:

```python
        lat = sidecar["latitude"] if sidecar else None
        lon = sidecar["longitude"] if sidecar else None
        country = None
        if lat is not None and lon is not None:
            country = geocoder.lookup(lat, lon)
            if country:
                located += 1
```

- In `set_plan(...)` delete `place=place,`.
- The final detail line becomes `detail += f" {located} carry a country."`.

`photolib/actions/organize.py` — in `_properties`, delete:

```python
    if row["place"]:
        props["place"] = row["place"]
```

`photolib/api/routes_review.py` — remove `"place",` from `ROW_FIELDS`.

- [ ] **Step 8: Remove place from the library read path**

`photolib/db/library_repo.py`:
- Docstring: `"Month, place, country, type"` → `"Month, country, type"`.
- `ROW_FIELDS`: delete `"place",`.
- `_SELECT`: `m.capture_time, m.capture_source, m.place, m.country,` → `m.capture_time, m.capture_source, m.country,`.
- `Filters`: delete `place: str | None = None`.
- `_where`: delete the `if filters.place:` clause.
- `facets()`: delete the `"places": group("m.place", ...)` entry.

`photolib/api/routes_library.py` — in `_filters`, delete the `place: str | None = None` parameter and `place=place,` from the `Filters(...)` call.

- [ ] **Step 9: Update the affected tests**

- `tests/test_media_repo.py`: remove `place="Warsaw",` from `set_plan` calls; change `assert row["place"] == "Warsaw"` to `assert row["country"] == "Poland"`; delete `with_place` assertions.
- `tests/test_api_review.py`: remove `place="Warsaw"` from the `set_plan` call (~line 30); delete `assert body["with_place"] == 1`.
- `tests/test_library_repo.py`: drop the place element from the seeding tuples and the `INSERT INTO media` column list; delete `test_filter_by_place`, `test_facets_omit_files_with_no_place`, and the `places` facet assertion (~line 163); change `assert detail["place"] == "Warsaw"` to `assert detail["country"] == "Poland"`; keep every country test.
- `tests/test_action_plan.py`: replace `test_place_is_absent_without_an_api_key` with:

```python
def test_country_is_absent_without_an_api_key(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["country"] is None
```

- `tests/test_action_organize.py`: `grep -n "place" tests/test_action_organize.py` — if any assertion expects a `place` upload property, delete that expectation.
- `tests/test_action_sync_tags.py` needs no change: `test_properties_organize_wrote_are_left_alone` seeds a foreign `place` property by hand, which is exactly the "leave non-`t_` properties alone" behavior it should keep testing.

- [ ] **Step 10: Sweep for stragglers and run the suite**

Run: `grep -rn "place" photolib/ | grep -v "places.py\|placehold"`
Expected: no hits referring to the removed column/filter (mentions inside `photolib/places.py` and words like "placeholder" are fine).

Run: `uv run pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add -A photolib tests
git commit -m "feat!: remove Place end to end; Country is the only geo facet"
```

---

### Task 3: Drive client learns capture-time fields

**Files:**
- Modify: `photolib/drive/client.py`
- Test: `tests/test_drive_client.py`

**Interfaces:**
- Produces: `DriveFile.capture_hint() -> int | None` (epoch seconds, UTC; EXIF `imageMediaMetadata.time` → `modifiedTime` → `createdTime` → None); `FILE_FIELDS` including `createdTime` and `imageMediaMetadata(time)`. Task 4 calls `capture_hint()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drive_client.py`:

```python
def test_capture_hint_prefers_exif_time():
    file = DriveFile(
        id="x", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"time": "2024:01:13 10:00:00"},
        modifiedTime="2026-01-01T00:00:00Z",
    )
    assert file.capture_hint() == 1705140000   # 2024-01-13T10:00:00Z


def test_capture_hint_falls_back_to_modified_time():
    file = DriveFile(
        id="x", name="a.mov", mimeType="video/quicktime",
        modifiedTime="2024-01-13T10:00:00Z",
    )
    assert file.capture_hint() == 1705140000


def test_capture_hint_survives_malformed_exif():
    file = DriveFile(
        id="x", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"time": "not a timestamp"},
        modifiedTime="2024-01-13T10:00:00Z",
    )
    assert file.capture_hint() == 1705140000


def test_capture_hint_of_an_undated_file_is_none():
    assert DriveFile(id="x", name="a", mimeType="image/heic").capture_hint() is None
```

(Import `DriveFile` at the top if the file does not already.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_drive_client.py -x -q`
Expected: FAIL — `imageMediaMetadata` unknown / `capture_hint` missing.

- [ ] **Step 3: Implement**

In `photolib/drive/client.py`:

```python
FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,createdTime,modifiedTime,parents,"
    "thumbnailLink,imageMediaMetadata(time)"
)
```

Add `from datetime import datetime, timezone` to the imports. On `DriveFile`, add fields:

```python
    created_time: str | None = Field(default=None, alias="createdTime")
    image_media_metadata: dict | None = Field(
        default=None, alias="imageMediaMetadata"
    )
```

and the method:

```python
    def capture_hint(self) -> int | None:
        """Best guess at when this was captured, in epoch seconds.

        EXIF time is the real answer where Drive extracted one; file times
        are the fallback for videos and stripped images. None means Drive
        knows nothing datable about this file.
        """
        exif = (self.image_media_metadata or {}).get("time")
        if exif:
            try:
                parsed = datetime.strptime(exif, "%Y:%m:%d %H:%M:%S")
                return int(parsed.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
        for stamp in (self.modified_time, self.created_time):
            if not stamp:
                continue
            try:
                return int(
                    datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                )
            except ValueError:
                continue
        return None
```

- [ ] **Step 4: Run to verify pass, then run the whole suite**

Run: `uv run pytest tests/test_drive_client.py -x -q` then `uv run pytest tests/ -x -q`
Expected: PASS (existing field-list assertions, if any, updated to the new `FILE_FIELDS`).

- [ ] **Step 5: Commit**

```bash
git add photolib/drive/client.py tests/test_drive_client.py
git commit -m "feat: Drive client reads EXIF and file times for a capture hint"
```

---

### Task 4: Scan stores `capture_hint` for destination files

**Files:**
- Modify: `photolib/db/schema.sql` (drive_files table), `photolib/db/migrations.py` (`_ADDED_COLUMNS`)
- Modify: `photolib/db/scan_repo.py` (`upsert_drive_files`, `record_drive_file`)
- Modify: `photolib/actions/scan_archives.py` (`_index_destination`)
- Modify: `tests/fakes/fake_drive.py` (`add_file` gains date params)
- Test: `tests/test_action_scan.py`

**Interfaces:**
- Consumes: `DriveFile.capture_hint()` from Task 3.
- Produces: `drive_files.capture_hint INTEGER` populated on every scan; `FakeDrive.add_file(..., modified_time: str | None = None, image_time: str | None = None)`; `ScanRepo.record_drive_file(..., capture_hint: int | None = None)`. Tasks 5–6 and 8 read the column.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_action_scan.py` (mirror its existing fixture usage — it builds a `ctx` with `FakeDrive` and runs `scan`):

```python
def test_scan_records_a_capture_hint_for_destination_files(ctx):
    ctx.drive.add_file(
        "hinted", "IMG_7.HEIC", b"x", parent="photos",
        mime_type="image/heic", modified_time="2024-01-13T10:00:00Z",
    )
    list(run(ctx, Params()))
    row = ctx.conn.execute(
        "SELECT capture_hint FROM drive_files WHERE drive_id = 'hinted'"
    ).fetchone()
    assert row["capture_hint"] == 1705140000
```

(Adapt `"photos"` to whatever the fixture names its destination root; if the fixture's `ctx` differs, follow the file's existing pattern.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_action_scan.py -x -q`
Expected: FAIL — `add_file` rejects `modified_time`, or `capture_hint` column/value missing.

- [ ] **Step 3: Implement**

`photolib/db/schema.sql` — in `CREATE TABLE IF NOT EXISTS drive_files`, add after `synced_tags TEXT`: `,
    capture_hint INTEGER` (keep valid SQL commas).

`photolib/db/migrations.py` — append to `_ADDED_COLUMNS`:

```python
    ("drive_files", "capture_hint", "capture_hint INTEGER"),
```

`tests/fakes/fake_drive.py` — extend `add_file`:

```python
    def add_file(
        self,
        id: str,
        name: str,
        content: bytes,
        parent: str,
        mime_type: str = "application/octet-stream",
        modified_time: str | None = None,
        image_time: str | None = None,
    ) -> DriveFile:
        file = DriveFile(
            id=id, name=name, mimeType=mime_type, size=len(content),
            md5Checksum=hashlib.md5(content).hexdigest(), parents=[parent],
            modifiedTime=modified_time,
            imageMediaMetadata={"time": image_time} if image_time else None,
        )
        self._files[id] = file
        self._content[id] = content
        return file
```

`photolib/db/scan_repo.py`:
- `upsert_drive_files`: add `capture_hint` to the INSERT column list and `?` row values (`r.get("capture_hint")`), and `capture_hint = excluded.capture_hint` to the `DO UPDATE SET` clause.
- `record_drive_file`: add keyword-only parameter `capture_hint: int | None = None`, include it in the INSERT columns/values and `DO UPDATE SET`.

`photolib/actions/scan_archives.py` — in `_index_destination`, the appended row dict gains `"capture_hint": child.capture_hint(),`.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_action_scan.py tests/test_scan_repo.py -x -q` then `uv run pytest tests/ -x -q`
Expected: PASS (existing `test_migrations` fresh-vs-upgraded identity still holds because both paths gain the column).

- [ ] **Step 5: Commit**

```bash
git add photolib/db/schema.sql photolib/db/migrations.py photolib/db/scan_repo.py \
        photolib/actions/scan_archives.py tests/fakes/fake_drive.py tests/test_action_scan.py
git commit -m "feat: scans record a capture hint for every destination file"
```

---

### Task 5: The bucket-packing module

**Files:**
- Create: `photolib/buckets.py`
- Test: `tests/test_buckets.py`

**Interfaces:**
- Consumes: `drive_files.capture_hint` (Task 4).
- Produces (Tasks 6 and 8 import all of these):

```python
TARGET_SIZE = 100
MAX_BUCKET = 130
UNKNOWN_FOLDER = "unknown-date"
def month_of(capture: int | None) -> str | None            # "YYYY-MM", UTC
@dataclass(frozen=True)
class Bucket:  months: tuple[str, ...]; count: int; name: str (property)
def pack(counts: dict[str, int]) -> list[Bucket]
def folder_map(counts: dict[str, int]) -> dict[str, str]   # month -> folder name
def unaccounted_drive_months(conn) -> Counter[str]         # live drive files with no media row
def library_histogram(conn) -> Counter[str]                # unaccounted + every media row
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_buckets.py`:

```python
from collections import Counter

from photolib.buckets import (
    UNKNOWN_FOLDER,
    folder_map,
    library_histogram,
    month_of,
    pack,
    unaccounted_drive_months,
)


def test_month_of_formats_utc_and_tolerates_none():
    assert month_of(1700000000) == "2023-11"
    assert month_of(None) is None


def test_small_months_pack_into_one_named_range():
    buckets = pack({"2022-01": 5, "2022-03": 4, "2023-02": 7})
    assert len(buckets) == 1
    assert buckets[0].name == "2022-01 - 2023-02"
    assert buckets[0].count == 16


def test_a_single_month_bucket_is_named_by_that_month():
    assert pack({"2026-05": 90})[0].name == "2026-05"


def test_a_bucket_closes_rather_than_growing_past_the_cap():
    buckets = pack({"2025-01": 90, "2025-02": 50})
    assert [b.name for b in buckets] == ["2025-01", "2025-02"]


def test_an_oversized_month_stands_alone():
    buckets = pack({"2026-04": 50, "2026-05": 182, "2026-06": 60})
    assert [b.name for b in buckets] == ["2026-04", "2026-05", "2026-06"]
    assert buckets[1].count == 182


def test_packing_ignores_insertion_order():
    counts = {"2024-02": 10, "2024-01": 10}
    assert pack(counts) == pack(dict(reversed(list(counts.items()))))
    assert pack(counts)[0].months == ("2024-01", "2024-02")


def test_folder_map_covers_every_month():
    mapping = folder_map({"2022-01": 5, "2022-02": 4})
    assert mapping == {
        "2022-01": "2022-01 - 2022-02",
        "2022-02": "2022-01 - 2022-02",
    }


def test_unknown_folder_name():
    assert UNKNOWN_FOLDER == "unknown-date"


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


def test_unaccounted_drive_months_skips_catalogued_trashed_and_undated(conn):
    _seed(conn)
    assert unaccounted_drive_months(conn) == Counter({"2024-01": 1})


def test_library_histogram_counts_media_and_unaccounted_files(conn):
    _seed(conn)
    assert library_histogram(conn) == Counter({"2024-01": 2})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_buckets.py -x -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `photolib/buckets.py`**

```python
"""Greedy month-packing: the folder layout of the Global Photos folder.

Folders hold whole months only, packed chronologically to roughly
TARGET_SIZE files. Whole months keep names meaningful (`2025-01 - 2025-03`);
the cap keeps folders browsable. Plan Organization and Reorganize both ask
this module, so a planned upload and an existing file can never disagree
about where a month belongs.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

TARGET_SIZE = 100
MAX_BUCKET = 130
UNKNOWN_FOLDER = "unknown-date"


def month_of(capture: int | None) -> str | None:
    if capture is None:
        return None
    return datetime.fromtimestamp(capture, tz=timezone.utc).strftime("%Y-%m")


@dataclass(frozen=True)
class Bucket:
    months: tuple[str, ...]
    count: int

    @property
    def name(self) -> str:
        if self.months[0] == self.months[-1]:
            return self.months[0]
        return f"{self.months[0]} - {self.months[-1]}"


def pack(counts: dict[str, int]) -> list[Bucket]:
    """Chronological greedy packing. A lone month may exceed MAX_BUCKET;
    a bucket never grows past it."""
    buckets: list[Bucket] = []
    months: list[str] = []
    total = 0
    for month in sorted(counts):
        count = counts[month]
        if months and total + count > MAX_BUCKET:
            buckets.append(Bucket(tuple(months), total))
            months, total = [], 0
        months.append(month)
        total += count
    if months:
        buckets.append(Bucket(tuple(months), total))
    return buckets


def folder_map(counts: dict[str, int]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for bucket in pack(counts):
        for month in bucket.months:
            mapping[month] = bucket.name
    return mapping


def unaccounted_drive_months(conn: sqlite3.Connection) -> Counter[str]:
    """Live Drive files no media row accounts for, by capture hint.

    These are the legacy files that predate the pipeline; the catalog knows
    nothing about them beyond what Drive itself reports.
    """
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT d.capture_hint FROM drive_files d "
        "LEFT JOIN media m ON m.drive_file_id = d.drive_id "
        "WHERE d.trashed_at IS NULL AND m.id IS NULL"
    ):
        month = month_of(row["capture_hint"])
        if month is not None:
            counts[month] += 1
    return counts


def library_histogram(conn: sqlite3.Connection) -> Counter[str]:
    """Every file the library will eventually hold, by month: catalogued
    media (uploaded or not) plus the unaccounted Drive files."""
    counts = unaccounted_drive_months(conn)
    for row in conn.execute("SELECT capture_time FROM media"):
        month = month_of(row["capture_time"])
        if month is not None:
            counts[month] += 1
    return counts
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_buckets.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add photolib/buckets.py tests/test_buckets.py
git commit -m "feat: greedy whole-month bucket packing for the destination layout"
```

---

### Task 6: Plan Organization targets buckets

**Files:**
- Modify: `photolib/actions/plan_organize.py`
- Modify: `photolib/actions/organize.py` (DESCRIPTION wording only)
- Test: `tests/test_action_plan.py`

**Interfaces:**
- Consumes: `buckets.month_of`, `buckets.folder_map`, `buckets.unaccounted_drive_months`, `buckets.UNKNOWN_FOLDER` (Task 5); country-only planner from Task 2.
- Produces: `media.target_folder` holds bucket names (e.g. `2019-01 - 2023-11`), never bare months. Task 8 relies on plan and reorganize computing identical folder maps.

- [ ] **Step 1: Update the tests to expect bucket names**

In `tests/test_action_plan.py`, the fixture yields two dated media files (2023-11 and 2019-01) and one hint-less legacy Drive file, so the packing makes one bucket named `2019-01 - 2023-11`. Rename and update:

```python
def test_target_folder_is_the_bucket_holding_the_capture_month(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["target_folder"] == "2019-01 - 2023-11"


def test_year_folder_fallback_is_recorded(ctx):
    list(run(ctx, Params()))
    row = by_name(ctx)["IMG_2.MOV"]
    assert row["capture_source"] == "year_folder"
    assert row["target_folder"] == "2019-01 - 2023-11"
```

(delete the old `test_target_folder_is_the_capture_month`). Add:

```python
def test_undated_media_land_in_the_unknown_folder(ctx):
    conn = ctx.conn
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z9', 'extra.zip', 1)"
    )
    # No year in the path, no sidecar, no archive mtime: nothing dates it.
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES "
        "((SELECT id FROM archives WHERE drive_id='z9'),"
        " 'Takeout/Google Photos/Album/IMG_9.MOV','IMG_9.MOV',"
        " 998,10,5,8,0,'media')"
    )
    conn.commit()
    MediaRepo(conn).upsert_media(
        conn.execute("SELECT id FROM entries WHERE crc32 = 998").fetchone()["id"]
    )
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_9.MOV"]["target_folder"] == "unknown-date"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_action_plan.py -x -q`
Expected: FAIL — targets are still bare months.

- [ ] **Step 3: Implement the two-pass planner**

In `photolib/actions/plan_organize.py`: add `from photolib import buckets` to the imports; delete the `_month` helper; restructure `run` so captures resolve before folders are chosen:

```python
    # Pass 1: resolve every capture, so the packing sees every file's month.
    resolved = []
    for row in rows:
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
        resolved.append((row, sidecar, capture, source))

    # The histogram covers what the library will hold: these rows, plus the
    # legacy Drive files no media row accounts for.
    counts = buckets.unaccounted_drive_months(ctx.conn)
    counts.update(
        month
        for _, _, capture, _ in resolved
        if (month := buckets.month_of(capture)) is not None
    )
    fmap = buckets.folder_map(counts)
```

Pass 2 is the existing loop, iterating `enumerate(resolved, start=1)` with `(row, sidecar, capture, source)` unpacked, minus the sidecar/archive lookups (done in pass 1), and with the folder line replaced by:

```python
        month = buckets.month_of(capture)
        folder = fmap.get(month, month) if month else buckets.UNKNOWN_FOLDER
```

Everything else — disambiguation via `taken`, geocoding, duplicate detection, `set_plan`, the summary detail — is unchanged from Task 2's version.

In `photolib/actions/organize.py`, update DESCRIPTION: `"Upload every planned file into its destination bucket folder, ..."` (rest unchanged), and the module docstring's "month folders" phrase to "bucket folders".

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_action_plan.py tests/test_action_organize.py -x -q` then `uv run pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/plan_organize.py photolib/actions/organize.py tests/test_action_plan.py
git commit -m "feat: Plan Organization targets ~100-file bucket folders"
```

---

### Task 7: `DriveWriter.move`

**Files:**
- Modify: `photolib/drive/writer.py`
- Modify: `tests/fakes/fake_drive.py`
- Test: `tests/test_drive_writer.py`

**Interfaces:**
- Produces (Task 8 calls this on both the real writer and the fake):

```python
def move(self, file_id: str, *, add_parent: str, remove_parent: str,
         name: str | None = None,
         properties: dict[str, str | None] | None = None) -> None
```

One PATCH: reparent, optionally rename, optionally set/clear appProperties (None deletes).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drive_writer.py`:

```python
def test_move_changes_parent_name_and_properties_in_one_call():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "f1"})

    writer_for(handler).move(
        "f1", add_parent="new-folder", remove_parent="old-folder",
        name="IMG~abc123.HEIC", properties={"place": None},
    )
    assert seen["params"]["addParents"] == "new-folder"
    assert seen["params"]["removeParents"] == "old-folder"
    assert seen["body"] == {
        "name": "IMG~abc123.HEIC", "appProperties": {"place": None},
    }


def test_move_without_a_rename_sends_no_name():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "f1"})

    writer_for(handler).move(
        "f1", add_parent="new-folder", remove_parent="old-folder",
        properties={"place": None},
    )
    assert seen["body"] == {"appProperties": {"place": None}}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_drive_writer.py -x -q`
Expected: FAIL — no `move` method.

- [ ] **Step 3: Implement**

In `photolib/drive/writer.py`, add a `# ---------- moving ----------` section:

```python
    @retry
    def move(
        self,
        file_id: str,
        *,
        add_parent: str,
        remove_parent: str,
        name: str | None = None,
        properties: dict[str, str | None] | None = None,
    ) -> None:
        """Reparent a file — and optionally rename it and adjust its private
        appProperties — in a single metadata-only call. No bytes move."""
        body: dict = {}
        if name is not None:
            body["name"] = name
        if properties:
            body["appProperties"] = properties
        response = self._http.patch(
            f"{API_ROOT}/files/{file_id}",
            params={
                "supportsAllDrives": "true",
                "fields": "id",
                "addParents": add_parent,
                "removeParents": remove_parent,
            },
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps(body),
        )
        raise_for_response(response)
```

In `tests/fakes/fake_drive.py`, add to the DriveWriter interface section:

```python
    def move(
        self,
        file_id: str,
        *,
        add_parent: str,
        remove_parent: str,
        name: str | None = None,
        properties: dict[str, str | None] | None = None,
    ) -> None:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        file = self._files[file_id]
        parents = [p for p in file.parents if p != remove_parent] + [add_parent]
        updates: dict = {"parents": parents}
        if name is not None:
            updates["name"] = name
        self._files[file_id] = file.model_copy(update=updates)
        if properties:
            self.update_properties(file_id, properties)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_drive_writer.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add photolib/drive/writer.py tests/fakes/fake_drive.py tests/test_drive_writer.py
git commit -m "feat: metadata-only move on the Drive writer"
```

---

### Task 8: The Reorganize action

**Files:**
- Create: `photolib/actions/reorganize.py`
- Test: `tests/test_action_reorganize.py`

**Interfaces:**
- Consumes: `buckets.*` (Task 5), `DriveWriter.move` (Task 7), `drive_files.capture_hint` (Task 4), `writer.trash`, `writer.ensure_folder`, `writer.update_properties`, `drive.list_children`, `drive.get_file`.
- Produces: action id `reorganize`, ORDER 45 (between Organize at 40 and Clear Stale Trees at 50); auto-discovered by the registry.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_action_reorganize.py`:

```python
import pytest

from photolib.actions import reorganize
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive

JAN_2024 = 1704067200          # 2024-01-01T00:00:00Z
DAY = 86400


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("photos", "Photos")
    fake.add_folder("f-old", "2023-12", parent="photos")
    fake.add_folder("f-back", "back_2024_01", parent="photos")
    fake.add_folder("f-empty", "2022-04", parent="photos")
    fake.add_file("d1", "IMG_1.HEIC", b"a", parent="f-old")
    fake.add_file("d2", "IMG_2.HEIC", b"b", parent="f-back")
    fake.add_file("d3", "IMG_1.HEIC", b"c", parent="f-back")
    return fake


@pytest.fixture
def ctx(conn, drive, tmp_path):
    # d1 is catalogued (misfiled in 2023-12); d2 and d3 are legacy files
    # dated only by their capture hints. All three belong in 2024-01.
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind) VALUES"
        " (1, 'p/IMG_1.HEIC', 'IMG_1.HEIC', 1, 1, 1, 8, 0, 'media')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, capture_time, target_folder, target_name,"
        " upload_status, drive_file_id)"
        " VALUES (1, ?, '2023-12', 'IMG_1.HEIC', 'done', 'd1')",
        (JAN_2024,),
    )
    files = [
        ("d1", "IMG_1.HEIC", "2023-12", "aaaaaa11", None),
        ("d2", "IMG_2.HEIC", "back_2024_01", "bbbbbb22", JAN_2024 + DAY),
        ("d3", "IMG_1.HEIC", "back_2024_01", "cccccc33", JAN_2024 + 2 * DAY),
    ]
    for drive_id, name, parent, md5, hint in files:
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
            " mime_type, capture_hint)"
            " VALUES (?, ?, ?, ?, 1, 'image/heic', ?)",
            (drive_id, name, parent, md5, hint),
        )
    conn.commit()
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )
    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=config, writer=drive,
    )


def _run(ctx, **params) -> list:
    return list(reorganize.run(ctx, reorganize.Params(**params)))


def _parent_names(drive, *file_ids) -> set[str]:
    return {
        drive.get_file(drive.get_file(fid).parents[0]).name for fid in file_ids
    }


def test_declares_itself_to_the_registry():
    assert reorganize.ID == "reorganize"
    assert isinstance(reorganize.ORDER, int)


def test_a_dry_run_moves_nothing(ctx, drive):
    messages = " ".join(event.message for event in _run(ctx))
    assert "confirm" in messages.lower()
    assert _parent_names(drive, "d1", "d2", "d3") == {"2023-12", "back_2024_01"}


def test_a_dry_run_names_the_moves(ctx):
    messages = [event.message for event in _run(ctx)]
    assert any("back_2024_01/IMG_2.HEIC" in m and "2024-01" in m for m in messages)


def test_confirm_moves_every_file_into_its_bucket(ctx, drive):
    _run(ctx, confirm=True)
    assert _parent_names(drive, "d1", "d2", "d3") == {"2024-01"}


def test_confirm_updates_the_local_index(ctx):
    _run(ctx, confirm=True)
    paths = {
        row["drive_id"]: row["parent_path"]
        for row in ctx.conn.execute("SELECT drive_id, parent_path FROM drive_files")
    }
    assert paths == {"d1": "2024-01", "d2": "2024-01", "d3": "2024-01"}


def test_confirm_updates_the_catalogued_plan(ctx):
    _run(ctx, confirm=True)
    row = ctx.conn.execute(
        "SELECT target_folder FROM media WHERE drive_file_id = 'd1'"
    ).fetchone()
    assert row["target_folder"] == "2024-01"


def test_name_collisions_are_renamed(ctx, drive):
    _run(ctx, confirm=True)
    names = {drive.get_file(fid).name for fid in ("d1", "d3")}
    assert names == {"IMG_1.HEIC", "IMG_1~cccccc.HEIC"}


def test_emptied_and_already_empty_folders_are_trashed(ctx, drive):
    _run(ctx, confirm=True)
    remaining = {f.name for f in drive.list_children("photos", folders_only=True)}
    assert remaining == {"2024-01"}


def test_the_place_property_is_stripped_from_moved_files(ctx, drive):
    drive.update_properties("d2", {"place": "Warsaw"})
    _run(ctx, confirm=True)
    assert "place" not in drive.app_properties("d2")


def test_the_place_property_is_stripped_from_unmoved_catalogued_files(ctx, drive):
    # Refile d1 so it is already where it belongs, then confirm.
    ctx.conn.execute(
        "UPDATE drive_files SET parent_path = '2024-01' WHERE drive_id = 'd1'"
    )
    ctx.conn.execute(
        "UPDATE media SET target_folder = '2024-01' WHERE drive_file_id = 'd1'"
    )
    ctx.conn.commit()
    drive.add_folder("f-new", "2024-01", parent="photos")
    drive.move("d1", add_parent="f-new", remove_parent="f-old")
    drive.update_properties("d1", {"place": "Warsaw"})

    _run(ctx, confirm=True)

    assert "place" not in drive.app_properties("d1")


def test_undated_files_go_to_the_unknown_folder(ctx, drive):
    drive.add_file("d9", "IMG_9.MOV", b"m", parent="f-back")
    ctx.conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
        " mime_type) VALUES ('d9', 'IMG_9.MOV', 'back_2024_01', 'dd', 1,"
        " 'video/quicktime')"
    )
    ctx.conn.commit()
    _run(ctx, confirm=True)
    assert _parent_names(drive, "d9") == {"unknown-date"}


def test_a_missing_writer_is_reported_not_crashed(ctx):
    ctx.writer = None
    events = _run(ctx)
    assert events[-1].level == "error"


def test_an_empty_index_asks_for_a_scan(ctx):
    ctx.conn.execute("DELETE FROM drive_files")
    ctx.conn.execute("DELETE FROM media")
    ctx.conn.commit()
    events = _run(ctx)
    assert events[-1].level == "error"
    assert "Scan" in events[-1].message
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_action_reorganize.py -x -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `photolib/actions/reorganize.py`**

```python
"""Move every live file into its ~100-file bucket folder, then tidy up.

Metadata-only: files are reparented with one `files.update` each — no bytes
are downloaded or re-uploaded. The same call renames arrivals that would
collide and strips the retired `place` property. Folders left empty are
trashed, never deleted.

Shaped like `sync_tags`: it reports everything it would do and changes
nothing until you confirm. Re-running after new uploads is expected — the
packing shifts as months fill, and reconciling is cheap.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Iterator

from photolib import buckets
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.drive.errors import DriveError

ID = "reorganize"
TITLE = "Reorganize Folders"
DESCRIPTION = (
    "Move every indexed file into its ~100-file bucket folder (whole months, "
    "packed greedily), renaming on collisions, clearing the retired place "
    "property, and trashing folders left empty. Reports what it would do "
    "unless you confirm."
)
ORDER = 45


class Params(ActionParams):
    confirm: bool = False


def _folder_paths(drive, root_id: str) -> dict[str, str]:
    """Every folder path under the root, mapped to its id. '' is the root."""
    paths = {"": root_id}
    stack: list[tuple[str, str]] = [(root_id, "")]
    while stack:
        current, path = stack.pop()
        for child in drive.list_children(current, folders_only=True):
            child_path = f"{path}/{child.name}" if path else child.name
            paths[child_path] = child.id
            stack.append((child.id, child_path))
    return paths


def _sweep_empty(drive, writer, folder_id: str) -> int:
    """Trash child folders that hold nothing, depth first. Never the root."""
    swept = 0
    for child in drive.list_children(folder_id):
        if not child.is_folder:
            continue
        swept += _sweep_empty(drive, writer, child.id)
        if not drive.list_children(child.id):
            writer.trash(child.id)
            swept += 1
    return swept


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if photos_root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0,
            level="error",
        )
        return

    rows = list(ctx.conn.execute(
        "SELECT d.drive_id, d.name, d.parent_path, d.md5, m.id AS media_id, "
        "       COALESCE(m.capture_time, d.capture_hint) AS capture "
        "FROM drive_files d LEFT JOIN media m ON m.drive_file_id = d.drive_id "
        "WHERE d.trashed_at IS NULL ORDER BY d.parent_path, d.name"
    ))
    if not rows:
        yield ProgressEvent(
            "Nothing indexed. Run Scan Archives first.", progress=1.0,
            level="error",
        )
        return

    fmap = buckets.folder_map(buckets.library_histogram(ctx.conn))
    targets: dict[str, str] = {}
    # Names already resident per target folder, so arrivals can dodge them.
    names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        month = buckets.month_of(row["capture"])
        target = fmap.get(month, month) if month else buckets.UNKNOWN_FOLDER
        targets[row["drive_id"]] = target
        if target == row["parent_path"]:
            names[target].add(row["name"])

    moves = [row for row in rows if targets[row["drive_id"]] != row["parent_path"]]

    per_folder = Counter(targets[row["drive_id"]] for row in rows)
    yield ProgressEvent(
        f"{len(moves)} of {len(rows)} file(s) would move, filling "
        f"{len(per_folder)} folder(s).",
        progress=0.1,
    )
    for folder in sorted(per_folder):
        yield ProgressEvent(f"{folder}: {per_folder[folder]} file(s)")
    for row in moves[:50]:
        yield ProgressEvent(
            f"would move {row['parent_path']}/{row['name']} "
            f"-> {targets[row['drive_id']]}"
        )

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — nothing was changed. Re-run with confirm to move "
            f"{len(moves)} file(s) and sweep empty folders.",
            progress=1.0,
            level="warn",
        )
        return

    try:
        folder_ids = _folder_paths(ctx.drive, photos_root.id)
        # ensure_folder must run sequentially: Drive would happily create the
        # same folder twice.
        for name in sorted({targets[row["drive_id"]] for row in moves}):
            if name not in folder_ids:
                folder_ids[name] = ctx.writer.ensure_folder(
                    photos_root.id, name
                ).id
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot prepare destination folders: {exc}", progress=1.0,
            level="error",
        )
        return

    moved = failed = renamed = 0
    for index, row in enumerate(moves, start=1):
        target = targets[row["drive_id"]]
        name = row["name"]
        if name in names[target]:
            stem, ext = os.path.splitext(name)
            name = f"{stem}~{(row['md5'] or row['drive_id'])[:6]}{ext}"
            renamed += 1
        try:
            old_parent = folder_ids.get(row["parent_path"])
            if old_parent is None:
                parents = ctx.drive.get_file(row["drive_id"]).parents
                old_parent = parents[0] if parents else photos_root.id
            ctx.writer.move(
                row["drive_id"],
                add_parent=folder_ids[target],
                remove_parent=old_parent,
                name=None if name == row["name"] else name,
                properties={"place": None},
            )
        except DriveError as exc:
            failed += 1
            yield ProgressEvent(f"{row['name']}: {exc}", level="error")
            continue
        names[target].add(name)
        ctx.conn.execute(
            "UPDATE drive_files SET parent_path = ?, name = ? WHERE drive_id = ?",
            (target, name, row["drive_id"]),
        )
        ctx.conn.execute(
            "UPDATE media SET target_folder = ?, target_name = ? "
            "WHERE drive_file_id = ?",
            (target, name, row["drive_id"]),
        )
        ctx.conn.commit()
        moved += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Moved {index} of {len(moves)}.",
                progress=0.1 + 0.7 * index / len(moves),
            )

    # Unmoved catalogued files still carry the property Organize once wrote.
    # A blind clear costs one call each and is a no-op where it is absent.
    cleared = 0
    for row in rows:
        if row["media_id"] is None or targets[row["drive_id"]] != row["parent_path"]:
            continue
        try:
            ctx.writer.update_properties(row["drive_id"], {"place": None})
            cleared += 1
        except DriveError as exc:
            yield ProgressEvent(f"{row['name']}: {exc}", level="error")

    try:
        swept = _sweep_empty(ctx.drive, ctx.writer, photos_root.id)
    except DriveError as exc:
        swept = 0
        yield ProgressEvent(f"Sweep stopped early: {exc}", level="error")

    detail = f"Moved {moved} file(s) into bucket folders."
    if renamed:
        detail += f" {renamed} renamed to avoid collisions."
    if cleared:
        detail += f" Cleared the retired place property from {cleared} file(s)."
    if swept:
        detail += f" Trashed {swept} empty folder(s)."
    if failed:
        detail += f" {failed} failed — re-run to retry them."
    yield ProgressEvent(detail, progress=1.0, level="warn" if failed else "info")
```

Note the pre-move `names[target]` seeding counts only files already in the target folder; unmoved-file rows keyed by `media_id`/`parent_path` drive the property-clearing pass.

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `uv run pytest tests/test_action_reorganize.py -x -q` then `uv run pytest tests/ -x -q`
Expected: PASS. If `tests/test_actions.py` asserts a fixed action roster, add `reorganize` (ORDER 45) to its expectations.

- [ ] **Step 5: Commit**

```bash
git add photolib/actions/reorganize.py tests/test_action_reorganize.py tests/test_actions.py
git commit -m "feat: Reorganize action repacks the destination into bucket folders"
```

---

### Task 9: Web UI — drop Place, rename the folder copy

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/lib/filters.ts`, `web/src/components/FilterSidebar.tsx`, `web/src/components/Lightbox.tsx`, `web/src/pages/ReviewPage.tsx`, `web/src/pages/SettingsPage.tsx`
- Test: `web/src/components/FilterSidebar.test.tsx`, `web/src/components/Lightbox.test.tsx`, `web/src/pages/LibraryPage.test.tsx`, `web/src/lib/filters.test.ts` (if it mentions place)

**Interfaces:**
- Consumes: the API shape from Tasks 2 (no `place`, no `places`, no `with_place`).
- Produces: a UI that compiles with the trimmed types.

- [ ] **Step 1: Trim the types**

`web/src/api/types.ts`: delete `with_place: number` from `ReviewSummary`; `place: string | null` from `ReviewMedia` and `LibraryFile`; `places: Facet[]` from `Facets`.

- [ ] **Step 2: Trim the filter model**

`web/src/lib/filters.ts`: delete `place?: string` from `LibraryFilters`, the `if (filters.place) params.set('place', filters.place)` line from `toQuery`, and the `if (filters.place) labels.push(filters.place)` line from `describe`.

- [ ] **Step 3: Trim the components**

- `FilterSidebar.tsx`: delete the whole `<FacetList title="Place" ... />` block.
- `Lightbox.tsx`: replace the Place `dt`/`dd` pair with:

```tsx
            <dt>Country</dt>
            <dd>{file.country ?? '—'}</dd>
```

- `ReviewPage.tsx`: delete `{ key: 'with_place', label: 'with a place' },` from the summary rows and the Place column — both the `<th>Place</th>` header cell and `<td>{row.place ?? '—'}</td>`.
- `SettingsPage.tsx`: change the help copy to `'Where organised photos will be placed, in date-range subfolders of ~100 files.'`

- [ ] **Step 4: Update the component tests**

- `FilterSidebar.test.tsx`: remove `places: [{ value: 'Warsaw', count: 4 }],` from the facets fixture; retitle `'lists places, types, and tags'` to `'lists countries, types, and tags'` and assert against the countries facet instead of Warsaw-as-place.
- `Lightbox.test.tsx`: remove `place: 'Warsaw',` / `place: null,` from fixtures; retitle `'shows capture date, place, and source archive'` to `'shows capture date, country, and source archive'`; assert the country renders.
- `LibraryPage.test.tsx`: remove `place: ...` from the three file fixtures and `places: [{ value: 'Warsaw', count: 1 }],` from the facets fixture.
- `filters.test.ts`: remove any `place` case.

- [ ] **Step 5: Verify**

Run: `cd web && npm test && npm run lint && npx tsc -b`
Expected: all green — the compiler is the real reviewer here; fix any leftover `.place` usage it finds.

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat: web UI drops the Place facet; Country stays"
```

---

### Task 10: Final sweep, docs, and verification

**Files:**
- Modify: `README.md` (destination-layout description)
- Verify: everything

- [ ] **Step 1: Sweep for stragglers**

Run: `grep -rni "place" photolib/ web/src/ tests/ README.md | grep -viE "places\.py|placehold|placed, in date|in place"`
Expected: nothing referring to the removed facet/column. Fix anything that slips through.

- [ ] **Step 2: Update README**

`grep -n "YYYY-MM\|month folder\|Place" README.md` and rewrite those sentences: the destination now holds whole-month bucket folders of ~100 files (`2025-01 - 2025-03`, `2026-05`, `unknown-date`), the Reorganize action repacks existing files (report-then-confirm, metadata-only moves), and geographic filtering is by Country only.

- [ ] **Step 3: Full verification**

Run: `uv run pytest tests/ -q` and `cd web && npm test && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: describe bucket folders and the Reorganize action"
```

---

## Self-review notes

- Spec §1 (bucketing rule, names, shared module) → Tasks 5, 6. §2 (dating uncatalogued files) → Tasks 3, 4. §3 (Reorganize) → Tasks 7, 8. §4 (Place removal) → Tasks 2, 9. §5 (testing) → embedded per task.
- Coupling forced Task 2 to span the whole backend: dropping the SQL column breaks every query naming it, so partial removal cannot keep the suite green.
- `SCHEMA_VERSION` is bumped once (Task 2); Task 4's added column needs no second bump because `migrate` is unconditional and idempotent.
- Type names cross-checked: `buckets.folder_map` / `month_of` / `unaccounted_drive_months` / `library_histogram` are used with identical signatures in Tasks 5, 6, 8; `move(file_id, *, add_parent, remove_parent, name, properties)` matches between Tasks 7 and 8.

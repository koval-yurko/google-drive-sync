# Repack the Global folder into ~100-file buckets; remove Place

Date: 2026-08-11. Status: approved.

## Problem

The Global Photos folder holds 2,562 files across 48 folders. 42 are `YYYY-MM`
month folders created by the pipeline (1,284 files, 26 folders with ≤10 files),
and 6 are legacy `back_*` folders (1,278 files with no catalog rows, so no
locally known capture dates). Separately, the Place facet has 104 distinct
values dominated by a long tail of 1–3-file places; Country (15 values) is the
useful geographic filter.

Goals:

1. Reorganize **all** files (month folders and `back_*` alike) into folders of
   roughly 100 files each with meaningful names.
2. Remove the Place filter and tag entirely; keep Country.

## Decisions (made with the operator)

- **Scheme C — greedy ~100-file range buckets**, refined to pack whole months.
- **`back_*` files are included**: extracted and restructured into the new
  convention; dates come from Drive metadata.
- **Full Place cleanup**: UI, API, DB column, upload property, and the existing
  `place` appProperties on Drive.

## Design

### 1. Bucketing rule — `photolib/buckets.py`

Sort live files chronologically by capture month, then greedily pack **whole
months** into buckets targeting ~100 files: close a bucket when adding the next
month would push it past ~130. A single month over 130 files stands alone.
Months are never split.

Folder names:

- Range bucket: `2022-01 - 2024-12` (first month, ` - `, last month)
- Single-month bucket: `2026-05`
- Files with no resolvable date: `unknown-date`, outside packing

Plan Organization and Reorganize both call this module, so planned uploads and
existing files always agree on destinations. Repacking may shift as new photos
arrive; re-running Reorganize reconciles with metadata-only moves.

### 2. Dating uncatalogued files

- Drive client `FILE_FIELDS` gains `createdTime` and `imageMediaMetadata(time)`.
- Migration adds `drive_files.capture_hint` (INTEGER epoch, nullable).
- The destination walk in Scan Archives fills it: EXIF `imageMediaMetadata.time`
  for images, else `modifiedTime`.
- Precedence when bucketing: `media.capture_time` (catalogued) →
  `drive_files.capture_hint` → `unknown-date`.

### 3. Reorganize action — `photolib/actions/reorganize.py`

Report-then-confirm, shaped like Sync Tags (`confirm: bool = False`).

1. Compute every live file's target bucket; diff against `parent_path`.
2. Report planned moves (and folder-level summary); stop unless confirmed.
3. On confirm, per file issue **one** `files.update` call that:
   - changes the parent (`addParents`/`removeParents`),
   - renames on a name collision in the destination (md5-suffix
     disambiguation),
   - strips the legacy `place` appProperty.
   Then update `drive_files.parent_path` (and name) and `media.target_folder`
   locally.
4. Sweep the destination: delete emptied folders (`back_*`, old months, and
   any already-empty folders).

Requires a new writer capability: move/update with parent change.

### 4. Place removal

- `Geocoder.lookup` returns country only; the geocache table is untouched (its
  `place` column simply goes unread).
- Migration drops `media.place`.
- Plan Organization stops resolving/storing place; Organize stops writing the
  `place` upload property.
- `Filters.place`, the `place` facet, the API query param, and the Place
  section in the web UI are removed. Country stays.

### 5. Testing

- Packing: small months merge, oversized month stands alone, name format,
  deterministic output for the same input.
- Reorganize: diff/report/confirm flow, collision renaming, local state
  updates, empty-folder sweep — against fake Drive objects.
- Migration: `capture_hint` added, `media.place` dropped, idempotent.
- Library: filters/facets without place; routes updated.

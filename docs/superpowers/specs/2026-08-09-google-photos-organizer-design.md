# Google Photos Organizer — Design

**Date:** 2026-08-09
**Status:** Approved

## Problem

A Google Takeout export of a Google Photos library sits in Google Drive as 17 ZIP
archives totalling 36.3 GB, plus three partially-extracted `Takeout/` trees left
over from earlier attempts. The photos are unusable in that form: they are locked
inside archives, their metadata is separated from the media, there is no
month-based organisation, and there is no way to label or filter them.

The goal is a local web application that extracts the media into a single
organised Drive folder, arranged by month, with duplicates skipped and both
automatic and manual tags applied.

## Observed data

These figures come from reading all 17 ZIP central directories over HTTP range
requests and sampling 84 sidecar files. They are the factual basis for the
design.

| Measurement | Value |
| --- | --- |
| ZIP parts | 17 (36.3 GB) |
| Media files | 1,284 |
| File types | 591 HEIC, 468 MOV, 158 PNG, 45 JPG, 21 MP4, 1 JPEG |
| JSON sidecars | 1,276 |
| Sidecars with no media anywhere | 0 |
| Sidecars whose media lives in a *different* ZIP part | 1,125 (88%) |
| Media with no sidecar | 15 (mostly `.MP4`, plus `(1)` copies) |
| Byte-identical duplicates within the ZIPs | 0 |
| Files in the three extracted trees | 255 media (6.44 GB) |
| Extracted-tree filenames also present in the ZIPs | 255 (100%) |
| Compression method | Deflate for all entries |
| Sampled sidecars with real coordinates | 67 of 84 (80%) |
| Upload origin | `IOS_PHONE` for all sampled files |
| Year range | 2022 – 2026 |

Four conclusions follow directly:

1. **No media is missing.** Every sidecar's media file exists somewhere in the
   archive set. The `url` field in the sidecars is irrelevant — and unusable
   anyway, because Google removed the `photoslibrary.readonly` scope on
   2025-03-31, so no OAuth credential can fetch an arbitrary Google Photos item.
   Only a logged-in browser session could, and it is not needed.
2. **Processing cannot be per-archive.** With 88% of sidecars separated from
   their media, the pipeline must build a global index before deciding anything.
3. **Exact-hash deduplication finds almost nothing inside the ZIPs.** The real
   redundancy is the 6.44 GB of stale extracted trees, whose filenames are a
   strict subset of the archive contents.
4. **Place tags are worthwhile.** 80% of files carry coordinates, spanning
   Warsaw, Lake Como, Lisbon, Madeira, Gdynia, Denmark and Malmö.

## Architecture

A FastAPI backend with a React/Vite single-page frontend, running on localhost
for a single user. A SQLite catalog holds all state. Long-running work executes
in a background job runner, not in request handlers.

```
React SPA  ──HTTP/SSE──>  FastAPI  ──>  Job runner  ──>  Actions
                             │                              │
                             └──────>  SQLite catalog  <─────┘
                                            │
                                     Drive API v3
```

### Action registry

Every long-running capability is one backend module that declares an id, title,
description, a Pydantic parameter schema, and a `run()` that yields progress
events:

```
photolib/actions/
├── scan_archives.py      → "Scan Archives"
├── pair_metadata.py      → "Pair Metadata"
├── plan_organize.py      → "Plan Organization"
├── organize.py           → "Organize Photos"
├── retag.py              → "Retag"
└── clear_stale_trees.py  → "Clear Stale Trees"
```

The frontend reads `/api/actions` and renders each as its own page with a
parameter form, a Run button, and a live progress/log panel. **Adding a future
action requires only a new module — its page appears automatically.** An action
may optionally supply a custom React page when it warrants a richer interface.

### Pages

| Page | Kind | Purpose |
| --- | --- | --- |
| Settings | static | Choose the Global Photos folder and the ZIP source folder via a Drive folder browser |
| Scan Archives | action | Index ZIP central directories and existing Drive content |
| Pair Metadata | action | Match sidecars to media across archive parts |
| Plan Organization | action | Resolve dates, duplicate verdicts, geocoding, target paths |
| Review Plan | static | Every file and its destination; approve before running |
| Organize | action | Streaming extract-and-upload run, resumable |
| Clear Stale Trees | action | Move the redundant extracted trees to Drive trash |
| Library | static | Thumbnail grid, lightbox, filtering, bulk tagging |
| Duplicates | static | What was skipped and why |
| Tags | static | Create, rename, merge, recolour, delete tags |
| Jobs | static | History, live progress, logs |

### Job runner

A single background worker consumes a queue, running one job at a time. Progress
and log lines stream to the SPA over SSE. Because per-file state is committed to
SQLite as work completes, closing the browser does not stop a run, and an
interrupted run resumes at the exact file it stopped on. Re-running a completed
job is a no-op.

### Media rendering

Chrome cannot display HEIC, and 591 files are HEIC. Rather than decoding
locally, the backend proxies Drive's own generated thumbnails — `=s400` for the
grid, `=s1600` for the lightbox — through an authenticated endpoint with a disk
cache. Videos play in Drive's embedded preview iframe, which handles HEVC `.MOV`
files that browsers refuse natively.

### Configuration

Stored in the catalog, never hardcoded:

- `photos_root` — the Global Photos folder, destination of all organised media.
- `zip_source` — the folder to fetch new archives from.

Both are chosen through a Drive folder browser in the UI. Re-running Scan picks
up newly dropped ZIPs, so the tool remains useful for future Takeout exports
rather than being a one-shot migration.

## Data model

SQLite (`photolib.db`):

| Table | Contents |
| --- | --- |
| `settings` | `photos_root`, `zip_source` — folder id and display name |
| `archives` | Drive id, name, size, modified time, last indexed |
| `entries` | Per ZIP entry: path, name, CRC32, uncompressed size, compressed size, compression method, local header offset, kind (`media` \| `sidecar`) |
| `sidecars` | Parsed JSON: title, `photoTakenTime`, `creationTime`, latitude, longitude, altitude, url, device, raw blob |
| `media` | Resolved capture time and its source, coordinates, place, target folder and name, duplicate verdict and reason, upload status, Drive file id, MD5, error text |
| `drive_files` | Existing Drive content including the stale trees: id, name, parent path, MD5, size |
| `tags` | Name, slug, colour, kind (`auto` \| `manual`) |
| `media_tags` | Assignment, marked `auto` or `manual` |
| `geocache` | Rounded coordinates → place, country, raw response |
| `jobs` | Action, params, status, progress, timestamps, error |
| `job_events` | Timestamped log lines per job |

The local header offset stored in `entries` is what makes single-file extraction
possible without downloading whole archives.

## Pipeline

### Scan Archives

For each ZIP in `zip_source`: fetch the final 1 MB, locate the End of Central
Directory record (falling back to the ZIP64 record when fields are saturated),
range-read the central directory, and parse every entry into `entries`. Also
walk `photos_root` and any sibling extracted trees into `drive_files`. Archives
unchanged by size and modified time are skipped on re-run.

### Pair Metadata

Extract and parse all sidecars — a range read of the entry's compressed bytes
followed by an inflate, roughly 1 KB each. Match each sidecar to its media by
directory plus stem, then by name anywhere in the corpus; the second pass is what
resolves the 88% cross-part case.

Two Takeout naming quirks must be handled:

- The duplicate index sits *outside* the extension: `IMG_7324.PNG` pairs with
  `IMG_7324(1).PNG`, and the sidecar may be named
  `IMG_7324.PNG.supplemental-metadata(1).json`.
- Sidecar filenames are truncated at 51 characters, so long names must match on
  the truncated prefix.

Unmatched sidecars are recorded with their reason rather than silently dropped.

### Plan Organization

Per media entry, in order:

1. **Capture date** — `photoTakenTime`, else embedded EXIF, else the Takeout
   year folder, else Drive `createdTime`. The source is recorded per file so the
   Review page can show which files used a fallback.
2. **Duplicate verdict** — identical CRC32 and size; `(N)` name variants; or a
   filename already present in `drive_files` from the stale trees. The archive
   copy wins; losers are marked `skipped` with a reason and are never uploaded.
   **Live Photo pairs — the same stem with both a HEIC and a MOV/MP4 — are
   explicitly exempt**, since they are two halves of one capture.
3. **Place** — coordinates rounded to roughly 1 km, `geocache` consulted, and the
   Google Geocoding API called only on a cache miss. The key is read from the
   `GOOGLE_MAPS_API_KEY` environment variable; with no key present, place tags
   are skipped and everything else proceeds. Given how heavily the coordinates
   cluster, 1,284 files should cost roughly 100–200 API calls.
4. **Target path** — `photos_root/YYYY-MM/<original name>`. Name collisions
   within a month are resolved by appending a short CRC suffix, never by
   overwriting.

This stage writes nothing to Drive and is re-runnable at will.

### Organize

The only mutating stage. Per file:

1. Range-read just that entry's compressed bytes.
2. Inflate in memory.
3. **Verify against the CRC32 from the central directory.**
4. Resumable-upload to the target month folder, computing MD5 during the stream.
5. Set `appProperties`: tags, capture time, place, source archive, source CRC.
6. **Compare the MD5 computed locally against the MD5 Drive returns.**
7. Mark the file `done`.

**Sidecar JSON files are never uploaded.** Their content is absorbed into the
catalog during Pair Metadata and onto the media file's `appProperties` during
Organize, so the organised library contains media only. The raw JSON is retained
in the catalog's `sidecars.raw_json` column, so nothing is lost.

A mismatch at either checkpoint marks the file `error` with its reason and moves
on; nothing half-succeeds silently. Month folders are created on demand and
cached by path. Work runs across a small pool of parallel workers with
exponential backoff and jitter on rate limits. Because the ZIP central directory
supplies a CRC32 for free and Drive returns an MD5 on upload, every byte is
verified twice without a single extra download.

## Tags

### Model

Each tag has a name, a URL-safe slug, a colour, and a kind:

- **Auto tags** are derived and namespaced: `place:warsaw`, `country:poland`,
  `year:2025`, `month:2025-05`, `device:ios`, `archive:part-003`. Regenerated by
  Plan and Retag; not hand-editable, because a re-run would overwrite them.
- **Manual tags** are user-created and arbitrary — `family`, `greece-2025`,
  `print-these`. No automated stage ever modifies them.

Both share one namespace for filtering, so `place:funchal` AND `family` is a
single query.

### Storage

Two locations, deliberately:

- **The catalog is the query engine.** Unlimited tags per file, instant
  multi-tag filtering, no API calls to search.
- **Drive `appProperties` is the durable mirror.** One property per tag
  (`t_family` = `1`), so tags survive the loss of this machine, travel with the
  file, and stay queryable through the Drive API by anything built later.

Drive permits 30 private appProperties per file and 124 bytes per key and value
combined. After the metadata properties (capture time, place, source CRC, source
archive), that leaves roughly **25 tags per file**. The UI displays a per-file
tag count and warns before the ceiling is reached rather than failing an API call
obscurely. Tag slugs are capped at 60 characters so `t_<slug>` always fits within
124 bytes.

### Bulk tagging

On the Library page:

1. Filter by month, place, file type, existing tag, or any combination.
2. Select files by click, shift-click for a range, or ⌘-click to toggle — or use
   **Select all matching this filter**, which selects the entire result set, not
   only the rendered rows.
3. A selection toolbar offers **Add tag** (existing, or a new one created on the
   spot), **Remove tag**, and the affected file count.

Large selections run as a background job, since each file needs its own Drive
`appProperties` update. The catalog updates immediately so the UI reflects the
change at once while Drive catches up. Small selections apply directly.

The Tags page handles create, rename, recolour, merge, and delete, with the file
count behind each tag. Renaming and merging rewrite Drive properties as a
background job.

## Library browser

A virtualised thumbnail grid over `photos_root`, lazy-loading on scroll and
grouped by month. Clicking a thumbnail opens a lightbox with the larger render,
capture date, place, coordinates, source archive, and editable tags. Videos play
in the embedded Drive preview. A sidebar carries filters for month, tag, place,
media type, and duplicate status.

## Error handling

- `403 rateLimitExceeded` and `429` and 5xx responses: exponential backoff with
  jitter, capped retries.
- `401`: refresh the OAuth token and retry once.
- Interrupted large uploads resume via the resumable upload session rather than
  restarting.
- CRC or MD5 mismatch: mark that file `error`; the Review page offers Retry.
- Geocoding failure: skip the place tag, never fail the file.
- Job crash: mark the job failed with its traceback; per-file progress is already
  committed, so a restart resumes cleanly.

### Destructive operations

Nothing is deleted implicitly. The stale extracted trees are skipped during
upload but left in place. Removing them is the separate `clear_stale_trees`
action, which shows exactly what it will affect, requires explicit confirmation,
and moves items to Drive's **trash** rather than deleting permanently. The source
ZIP archives are never modified.

## Testing

Test-driven throughout.

- **Pairing against real data.** The Scan action's output for the 17 real
  archives — 2,560 entry names carrying every genuine Takeout quirk — is captured
  as a fixture. Pairing logic is then asserted against reality: 1,276 sidecars,
  0 unmatched, 1,125 cross-part. No network access required.
- **ZIP reader.** Synthetic archives built in-process with known CRCs, covering
  deflate and stored entries, ZIP64 records, `(N)` names, and truncated sidecar
  names.
- **Pipeline and actions.** A fake Drive client backed by an in-memory dict,
  implementing the same interface as the real one, so the full pipeline runs
  offline in milliseconds.
- **API.** FastAPI `TestClient` over every route.
- **Frontend.** Vitest for grid selection and filter logic.
- **Live integration.** Real Drive tests against a scratch folder exist but are
  opt-in and skipped by default.

## Build order

Each phase ends with something usable.

| Phase | Delivers | Enables |
| --- | --- | --- |
| 1. Foundation | Drive client, ZIP reader, catalog, app skeleton, job runner, Settings page with folder picker | Point the app at your folders |
| 2. Knowledge | Scan, Pair, Plan actions and the Review page | See where all 1,284 files would go, with zero risk |
| 3. Move | Organize action, resumable; Clear Stale Trees | Run the 36 GB migration |
| 4. Browse and tag | Library, Tags, Duplicates pages | View, filter, and bulk-tag everything |

Phase 2 is the natural checkpoint: it answers "is this correct?" before a single
byte moves.

## Out of scope

- Perceptual or video-fingerprint duplicate detection. Exact matching plus name
  variants was chosen; the data shows no byte-identical duplicates inside the
  archives, so the remaining redundancy is the extracted trees and seven `(N)`
  copies.
- Fetching media from `photos.google.com` URLs. Unnecessary — no media is
  missing — and impossible via OAuth since the March 2025 scope removal.
- Uploading back into Google Photos. The organised library lives in Drive.
- A local copy of the library. Media streams from archive to Drive without being
  stored on this machine.
- Multi-user access, authentication, or remote deployment. Single user,
  localhost.

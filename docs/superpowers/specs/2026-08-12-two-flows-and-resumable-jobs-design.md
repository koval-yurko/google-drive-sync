# Two flows, rule-driven metadata, and resumable jobs

Date: 2026-08-12. Status: approved.

## Problem

The app exposes eleven actions in a strict pipeline order. Running it means
knowing that Scan comes before Pair, Pair before Plan, Plan before Organize,
and that Reorganize, Sync Tags and Clear Duplicates are separate follow-ups
each with its own confirm step. That sequence is an accurate description of
the implementation and a poor description of the two things the operator
actually wants to do:

1. **Get files out of the archives and into the Global folder**, without
   uploading what is already there.
2. **Make the Global folder tidy** — every file in its bucket folder, dated,
   located, tagged, and with duplicates gone — whatever route the file took
   to get there.

Three further gaps:

- **Files that arrive from outside the archives have no metadata.** The
  catalog only knows a capture date and country for files it planned itself.
  Anything else shows nulls in the Library.
- **A failed job is dead.** `media.upload_status` lets Organize skip finished
  uploads, but Scan, Pair, Plan, Reorganize and Sync Tags all restart from
  nothing. There is no Resume in the UI, and no Cancel at all — the `jobs`
  table allows a `cancelled` status that nothing can ever set.
- **Duplicates are uploaded on purpose.** Plan records a duplicate verdict and
  uploads anyway. That was the right call for the first bulk migration; it is
  the wrong call for the incremental top-ups that follow.

Library browsing and tagging are not a problem — they work, and this design
leaves them alone.

## Decisions (made with the operator)

- **Two flows plus an Advanced section.** The nine existing steps stay
  registered and reachable; the flows orchestrate them rather than replacing
  them. Nothing is deleted.
- **Both flows are report-then-confirm**, end to end, matching the shape
  `reorganize` and `sync_tags` already use.
- **Skip evidence is content, with the name as a hint.** A file is skipped
  only when its bytes are already in the Global folder — never on name alone.
- **Metadata comes from Drive's own EXIF and from `appProperties`.** No new
  rule engine, no filename parsing. The date, country and bucket rules the app
  already has are the rules.
- **Resume means per-item checkpoints.** A resumed job skips items already
  done, in every phase.
- **Two additions beyond the flows:** Cancel a running job, and Verify
  Library. Near-duplicate detection and manifest export are explicitly out of
  scope.

## Design

### 1. Action taxonomy — `photolib/actions/registry.py`, `web/src/components/Nav.tsx`

`ActionSpec` gains `group: str`, read from an optional module-level `GROUP`
attribute and defaulting to `"advanced"`. The registry does not validate the
value beyond the two the Nav knows about; an unrecognised group renders under
Advanced.

The Nav renders **Flows** first, then the existing views, then **Advanced** in
a collapsed `<details>`.

| Group | Actions |
| --- | --- |
| Flows | `sync_archives` — Sync from Archives; `reorganize_library` — Reorganize Folders |
| Views | Library, Tags, Review, Jobs, Settings — unchanged |
| Advanced | `check_connection`, `scan_archives`, `pair_metadata`, `plan_organize`, `organize`, `reorganize`, `sync_tags`, `clear_duplicates`, `clear_stale_trees`, `verify_library` |

`reorganize` keeps its ID and its behaviour but is retitled **Repack Buckets**,
freeing the name "Reorganize Folders" for the flow. `ORDER` now sorts *within*
a group rather than across all actions, so the existing values (0, 10, 20, …)
are left alone and the flows take 1 and 2.

The README's "Adding an action" section documents `GROUP`.

### 2. Composing sub-actions — `photolib/actions/phases.py`

A flow drives the existing `run(ctx, params)` generators. One helper does the
plumbing and nothing else:

```python
def run_phase(
    name: str, span: tuple[float, float], runner, ctx, params, *, index, total
) -> Iterator[ProgressEvent]
```

It re-yields every event from `runner(ctx, params)` with `progress` rescaled
from `[0, 1]` into `span`, `phase` set to `name`, and the message left
untouched — the phase name travels in the field, not smeared into the text.
`index` and `total` are the phase's position in the flow, carried on the event
so the UI can render `phase 5 of 5` without knowing what the phases are.
A phase that yields `progress=None` passes through unchanged. An `error`-level
event does not by itself stop the flow; the flow decides, because some phases
report per-item errors that are not fatal.

`params` is the sub-action's own `Params`, constructed by the flow. Where a
sub-action has a `confirm` field of its own (`reorganize`, `clear_duplicates`),
the flow passes its own `confirm` straight down, so the flow's single gate is
the only gate the operator ever sees.

This is the whole integration mechanism. Sub-actions remain unaware they are
being composed, and their Advanced pages keep working on the same code.

### 3. Sync from Archives — `photolib/actions/sync_archives.py`

```python
GROUP = "flow"; ORDER = 1
class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None
    limit: int | None = None
    workers: int = 4
    retry_errors: bool = False
```

| Phase | Span | Runs |
| --- | --- | --- |
| Connect | 0.00 – 0.02 | `check_connection.run` |
| Scan | 0.02 – 0.25 | `scan_archives.run` |
| Pair | 0.25 – 0.45 | `pair_metadata.run` |
| Plan | 0.45 – 0.55 | `plan_organize.run` |
| — | | Summary; stop unless `confirm` |
| Upload | 0.55 – 1.00 | `organize.run` |

The unconfirmed run ends with a summary — files to upload, files already
present, files with no resolvable date, files in error — and a pointer to the
Review page, which already renders exactly this plan. Confirming re-runs the
flow with `confirm=true` and the same `run_id`: the earlier phases find their
work already checkpointed (§5) and pass through in seconds, so confirming does
not re-scan 17 archives.

A phase that yields a fatal `error` event ends the flow at that phase. The job
is marked `failed` and is resumable.

### 4. The skip rule — `photolib/actions/plan_organize.py`, `photolib/transfer.py`

Plan gains a `media.plan_verdict` column with three values. It is a separate
column from `upload_status` on purpose: SQLite cannot alter a CHECK
constraint, so extending the `upload_status` enum would mean rebuilding the
`media` table; and a planning verdict is a different fact from an upload's
state anyway.

- **`skip`** — the entry's `(crc32, size)` matches a `media` row that reached
  `done` with a Drive-confirmed MD5, and that row's `drive_file_id` is still
  present and untrashed in `drive_files`. Certainty at zero cost: the app
  uploaded these bytes itself and verified them.
- **`verify`** — a live file under `photos_root` has the same name *and* the
  same size as the entry, but no CRC32 evidence. The ZIP central directory
  holds CRC32; Drive holds MD5; neither can be derived from the other without
  the bytes. So the verdict is deferred to transfer time.
- **`upload`** — no live file of that name. Upload unconditionally.

`transfer_entry` already inflates the entry to a temp file and checks its
CRC32. It computes MD5 over the same byte stream at the same time, at
effectively no cost. For a `verify` row, Organize then compares that MD5
against the candidate's `drive_files.md5`:

- equal → the upload is skipped, `media.drive_file_id` is pointed at the
  existing Drive file, `upload_status` becomes `done`, and the temp file is
  deleted;
- different → the file is uploaded under a disambiguated name, using the
  existing md5-suffix scheme in `plan_organize._disambiguate`.

Bytes therefore come **down** to prove identity but never go **up**
needlessly. The expensive, quota-consuming half is what gets skipped. Two
genuinely different photos named `IMG_1234.HEIC` — routine across iPhone
resets — both survive.

Rows with `plan_verdict = 'skip'` are excluded from Organize's work set and
reported in the summary. The Review page gains a verdict column and a filter,
so "what will be skipped" is inspectable before confirming.

Re-planning recomputes every verdict from scratch, as Plan already does.

### 5. Checkpoints, Resume and Cancel — `photolib/db/job_items_repo.py`, `photolib/jobs/runner.py`

One table serves as both the checkpoint ledger and the persisted dry-run plan.

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

`jobs` gains `run_id TEXT`, `resumed_from TEXT`, `phase TEXT`,
`items_done INTEGER NOT NULL DEFAULT 0`, `items_total INTEGER NOT NULL DEFAULT 0`.

**Run identity.** A flow run gets a `run_id` (a fresh uuid when `Params.run_id`
is absent). Every job records it. Resuming creates a *new* job with the *same*
`run_id`.

**The contract.** Each phase enumerates its work as `job_items` rows keyed by a
stable identity — Drive file id, `entries.id`, `archives.drive_id` — and
processes only the rows that are not `done`. An item is flipped to `done` in
the same transaction that records its effect, or to `failed` with the error in
`detail`. `item_key` must be stable across runs; a row index is not an
acceptable key.

That is the whole "never repeat the same work" guarantee, and it now covers
every phase rather than only uploads.

**The plan is the checkpoint** — for Reorganize. Its Dedupe, Repack and Sweep
phases write `pending` rows whose `detail` holds the JSON of the intended
operation. Confirm reads those rows back by `run_id` and executes exactly what
the operator read, flipping each to `done`. Dry-run persistence and
resumability are one mechanism, so a confirmed run that dies halfway resumes
without re-planning, and confirming can never act on a plan different from the
one displayed. If a confirm run is given a `run_id` with no rows for those
phases, it says so and stops rather than silently re-planning — the operator
must produce a fresh plan and read it.

Sync from Archives does not need that: its plan already lives in the `media`
table, which Review renders. Its `job_items` are pure checkpoints — archives
indexed, sidecars paired, entries planned, entries transferred — and its
Upload phase enumerates its own items on the confirm run, so an absent set of
Upload rows is the normal case, not an error.

**Cancel.** `POST /api/jobs/{id}/cancel`. The runner owns a
`threading.Event` per running job, exposed on `ActionContext.cancelled` so
fan-out workers (Organize's `ThreadPoolExecutor`) stop taking new work. The
runner checks the event between yields and calls `gen.close()`, marks the job
`cancelled`, and leaves `job_items` untouched. Cancellation lands on item
boundaries — an item either completes or it does not — which is what makes
Cancel and Resume the same mechanism. A queued job is cancelled without ever
starting.

**Resume.** `POST /api/jobs/{id}/resume` creates a new job with the same
action, params and `run_id`, and `resumed_from` set to the old job id.
Offered only for `failed` and `cancelled` jobs. There is no automatic resume
on process start: a crash loop that re-runs itself unattended is worse than a
button.

**Progress.** `ProgressEvent` gains `phase: str | None`, `done: int | None`,
`total: int | None`; the runner persists them onto the job row.
`JobProgress` shows `Upload · phase 5 of 5 · 412 / 842`; `JobsPage` shows the
phase and offers Resume on `failed`/`cancelled` rows and Cancel on
`queued`/`running` ones.

### 6. Reorganize Folders — `photolib/actions/reorganize_library.py`

```python
GROUP = "flow"; ORDER = 2
class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None
```

One flow, one confirm gate covering all five phases.

| Phase | Span | Does |
| --- | --- | --- |
| Index | 0.00 – 0.20 | Walk `photos_root`, upsert `drive_files` |
| Enrich | 0.20 – 0.45 | Fill dates, coordinates, country and tags |
| Dedupe | 0.45 – 0.60 | Group by Drive MD5, choose keepers, trash the rest |
| Repack | 0.60 – 0.90 | `reorganize.run` — metadata-only moves into buckets |
| Sweep | 0.90 – 1.00 | Trash folders left empty |

**Index.** `_index_destination` moves out of `scan_archives` into a new
`photolib/scan.py` and is called by both, so this flow never re-reads 17 ZIP
central directories. Its `job_items` are keyed by folder id, so an interrupted
walk resumes per folder.

`FILE_FIELDS` in `photolib/drive/client.py` grows
`imageMediaMetadata(time,location,cameraMake,cameraModel)`,
`videoMediaMetadata(durationMillis)` and `appProperties`. Fetching
`appProperties` in the list response also lets `sync_tags` drop its per-file
`GET`, which is a straight speedup for an action that currently makes one call
per tagged file.

**Enrich** writes only to the catalog:

- `drive_files.capture_hint` from `imageMediaMetadata.time`, else
  `videoMediaMetadata` creation time, else `createdTime` — the precedence
  `buckets` already documents, extended to video.
- `drive_files.latitude` / `longitude` from `imageMediaMetadata.location`, and
  `country` through the existing `places.Geocoder` and its `geocache` table.
  No new geocoding rules; the same lookup Plan uses.
- `drive_files.metadata_source` records which of the above supplied the date,
  so the Library can distinguish a real EXIF date from a file-creation
  fallback.
- `t_*` `appProperties` are folded back into `file_tags`, creating any tag
  that does not exist locally (name derived from the slug, default colour).
  This makes Drive the durable source of truth for tags and makes a catalog
  rebuild non-destructive. A local tag absent from Drive is left alone —
  Enrich only adds; `sync_tags` remains the only thing that removes.

`LibraryRepo` then selects `COALESCE(m.capture_time, d.capture_hint)` and
`COALESCE(m.country, d.country)`, so files that never came through an archive
stop showing nulls in the month and country facets.

**Dedupe** reuses `clear_duplicates`' rules unchanged: group live files by the
MD5 Drive itself reports, never the catalog's; one copy of each group always
survives, preferring a copy this pipeline uploaded and verified, otherwise the
first by folder and name; zero-byte files share an MD5 without being copies of
anything, so they are reported and left alone; and it trashes, never deletes.

Dedupe runs **before** Repack so the packer does not allocate bucket space for
files that are about to disappear — otherwise every dedupe run would leave the
bucket layout wrong and require a second Repack.

**Repack** and **Sweep** are `reorganize.run` as it stands today.

### 7. Verify Library — `photolib/actions/verify_library.py`

Read-only, Advanced, no confirm. Walks the live contents of `photos_root` and
reports catalog drift:

- `media` rows marked `done` whose `drive_file_id` is missing or trashed —
  deleted outside the app;
- rows whose `drive_files.parent_path` no longer matches `media.target_folder`
  — moved outside the app;
- rows whose Drive MD5 no longer matches the recorded `media.md5`;
- rows marked `done` with no MD5 ever confirmed;
- `file_tags` rows pointing at drive ids not present in `drive_files`.

It writes nothing and prescribes nothing. Each category is reported with a
count and the first twenty examples.

### 8. Migrations — `photolib/db/migrations.py`

`SCHEMA_VERSION` becomes 6. `schema.sql` gains `job_items`; the added-column
list gains:

| Table | Column |
| --- | --- |
| `media` | `plan_verdict TEXT` |
| `jobs` | `run_id TEXT`, `resumed_from TEXT`, `phase TEXT`, `items_done INTEGER NOT NULL DEFAULT 0`, `items_total INTEGER NOT NULL DEFAULT 0` |
| `drive_files` | `country TEXT`, `latitude REAL`, `longitude REAL`, `metadata_source TEXT` |

No table rebuilds and no CHECK constraint changes. `media.plan_verdict` is
deliberately unconstrained in `schema.sql` too, not just in the `ALTER`: the
from-scratch and upgraded schemas must be identical, and
`tests/test_migrations.py` asserts exactly that. The three verdict values are
validated in code, in the one place that writes them.

### 9. API surface

| Route | Purpose |
| --- | --- |
| `POST /api/jobs/{id}/cancel` | Set the cancel event; 409 if the job is already finished |
| `POST /api/jobs/{id}/resume` | New job, same `run_id`; 409 unless `failed` or `cancelled` |
| `GET /api/jobs/{id}/items?phase=&state=` | The checkpoint ledger, for the plan view and for debugging |

`GET /api/actions` includes `group` in each spec. Job payloads include
`run_id`, `phase`, `items_done`, `items_total`.

### 10. Testing

Drive is faked by `tests/fakes/fake_drive.py` and ZIPs are built in memory;
everything below stays offline.

- **`phases`**: progress rescaling into a span, `None` progress passing
  through, phase name attached to every event.
- **Skip rule**: each of the three verdicts from a constructed catalog; a
  `verify` row whose MD5 matches (upload skipped, `drive_file_id` adopted);
  one whose MD5 differs (uploaded, name disambiguated); an entry whose
  `(crc32, size)` matches a `done` row whose Drive file has been trashed
  (must *not* skip).
- **Checkpoints**: a flow that fails on item 3 of 5, resumed, asserting items
  1–2 are not re-processed and 3–5 are; `item_key` stability across runs;
  a confirm run against a `run_id` with no `pending` rows.
- **Cancel**: a cancelled job leaves `job_items` intact, is marked
  `cancelled`, and its generator is closed; a queued job cancels without
  starting; cancel on a finished job is a 409.
- **Enrich**: EXIF present, EXIF absent, video-only metadata, GPS present and
  absent; `t_*` appProperties creating a new local tag; a local tag absent
  from Drive surviving untouched.
- **Reorganize flow ordering**: a duplicate group whose losers are trashed
  before packing, asserting the surviving bucket counts.
- **Verify Library**: one fixture per drift category.
- **Frontend**: Nav renders Flows and Advanced; `JobProgress` renders phase
  and item counts; Resume and Cancel appear only for the right statuses.

## Out of scope

Near-duplicate and Live Photo pair detection; manifest export; automatic
resume on process start; filename-pattern date parsing; a user-defined
tagging rule engine. Each is a separate spec if it is ever wanted.

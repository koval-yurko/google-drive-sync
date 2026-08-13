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

## The two flows

Most runs need only two actions, both under **Flows** in the sidebar. Each
reports a full plan and changes nothing in Drive until you confirm through the
button on the run page.

### Sync from Archives

Four read-only phases build a plan; a fifth, gated by `confirm`, uploads it.

| Phase | Does |
| --- | --- |
| Connect | Verifies credentials and folders |
| Scan | Indexes archive contents and the destination folder |
| Pair | Matches sidecars to media across archive parts |
| Plan | Resolves dates, countries, duplicate verdicts, destinations |

After Plan it reports how many files are pending, already in the Global
folder, or in error, and stops — nothing has been uploaded yet. Open
**Review Plan** to see every file and where it would go, then click the
**Confirm this plan** button on the run page. The button includes the run id
shown in the progress display, so confirming acts on exactly the plan you read.
If you need to confirm an older run, enter its run id in the form and set
`confirm` to execute the fifth phase, **Upload**, the only one that writes to
Drive.

Files whose bytes are already in the Global folder are **not** uploaded
again. A file is skipped only on content evidence — an MD5 this app recorded,
or one computed from the archive at transfer time. A file whose name matches
but whose bytes differ is uploaded under a disambiguated name.

### Reorganize Folders

Five phases behind one confirm gate: Index, Enrich, Dedupe, Repack, Sweep.

- **Index** re-walks the Global Photos folder.
- **Enrich** fills in dates, countries and tags for files this catalog has
  not looked at yet (see "Browsing and tagging" below).
- **Dedupe** plans which duplicate copies to trash.
- **Repack** plans which files move into the bucket folder matching their
  month.
- **Sweep** trashes folders left empty once Dedupe and Repack have run.

Without `confirm` it reports the Dedupe and Repack plans and changes
nothing. Click the **Confirm this plan** button on the run page to apply both
plans, in that order — a file about to be trashed must never reserve space in a
bucket — then sweeps. The button includes the run id shown in the progress
display, so confirming acts on exactly the plan you read. Repack moves are
metadata-only: no bytes are re-downloaded or re-uploaded.

Organised photos live in whole-month bucket folders under `Photos/`, packed
greedily to roughly 100 files each: a busy month gets its own folder
(`2026-05`), quiet months share one (`2025-01 - 2025-03`), and files with no
resolvable date land in `unknown-date`. Because the packing depends on how
many files a month ends up holding, later uploads can shift where a month's
bucket falls — Repack corrects for that. The existing `back_*` folders are
indexed for duplicate detection and otherwise left alone; Repack folds their
files into buckets and trashes them once empty, same as everything else.

## Advanced

The phases above also exist as their own actions, for re-running a single
phase in isolation or for recovering a flow that needs a nudge partway
through.

| Action | Does | Writes to Drive |
| --- | --- | --- |
| Check Connection | Verifies credentials and folders | No |
| Scan Archives | Indexes archive contents and the destination folder | No |
| Pair Metadata | Matches sidecars to media across archive parts | No |
| Plan Organization | Resolves dates, countries, duplicate verdicts, destinations | No |
| Organize Photos | Uploads every planned file into its destination bucket folder | Yes |
| Repack Buckets | Moves every indexed file into its bucket folder, trashing what's left empty | Yes |
| Sync Tags to Drive | Mirrors tags onto each file's `appProperties` | Yes |
| Verify Library | Compares the catalog against what Drive actually holds and reports drift | No |

## Resuming and cancelling

The two flows checkpoint differently, matched to how expensive each phase is
to redo.

Reorganize Folders' five phases record their work in `job_items` — one row
per file for Enrich, Dedupe and Repack, one row for the whole folder for
Index — marked `done` (or `failed`, or `skipped`) as it happens. Dedupe's
and Repack's plans live there too, and confirming applies exactly what was
reported rather than recomputing it.

Sync from Archives doesn't need that: its four read-only phases (Connect,
Scan, Pair, Plan) are cheap enough to simply re-run in full every time —
Scan skips any archive whose index is already current, and Plan clears and
recomputes the whole per-file plan into the `media` table on every pass, so
there is nothing to check before redoing it. The one `job_items` row Sync
writes is a sentinel with no per-file detail; its only job is telling a
confirm run "the plan you read exists" from "there is no plan to confirm
yet." Upload — the phase that actually writes to Drive, and the one place a
Sync run genuinely resumes rather than starts over — checkpoints per file in
the `media` table instead (`upload_status`, `upload_session_uri`; see
"Running the migration", below), not in `job_items`.

**Cancel** (`POST /api/jobs/{id}/cancel`) stops the job at the next item
boundary, not mid-item, and leaves whatever it had checkpointed — in
`job_items` or in `media`, whichever that phase uses — exactly as it was.
**Resume** (`POST /api/jobs/{id}/resume`) starts a new job on the same run:
work that checkpoint already shows as done is skipped, so a cancelled or
failed job picks up close to where it left off rather than starting over.

There is no automatic resume on restart: nothing watches for a run the
server was in the middle of when it went down and picks it back up on its
own. That is deliberate — a partially-applied write should get a look before
anything acts on it further, not a silent continuation the moment the server
comes back.

## Running the migration

Organize is resumable and re-runnable. Every file is verified twice: against the
CRC32 in the ZIP index before a byte is uploaded, and against the MD5 Drive
returns afterwards. A file that fails either check is marked `error` and left
for you to retry from the Review page; it is never half-written.

- `workers` (default 4) — parallel uploads. Drop it to 1 if Drive throttles.
- `limit` — cap the batch. Worth setting for the first run.
- `retry_errors` — include previously failed files. Off by default, so an
  unattended re-run never silently re-attempts a file that failed for a reason.

Closing the browser does not stop a run. Killing the process is safe: finished
files are skipped on the next run and an interrupted upload resumes from
whatever Drive confirms it holds.

## Watching a run

Every Organize run gets its own folder under `downloads/`, named for the time
it started:

```
downloads/2026-08-10_14-32-05/IMG_1234.HEIC.part
```

A file appears there while it is being pulled out of the archive and
disappears the moment its upload is verified, so what you see is what is
genuinely in flight. `ls -lh downloads/*/` is a complete progress report. The
Organize page shows the same thing, adding the upload half: a file that has
finished downloading sits at full size on disk while its bytes go to Drive.

A run only ever touches its own folder. If a run dies, its folder stays,
named for when it started, and the next run reports it rather than deleting
it — `rm -rf downloads/<that folder>` when you have looked. Empty leftovers
are cleared automatically.

`downloads/` is gitignored, and nothing is kept there after a successful
upload.

## Browsing and tagging

The Library page shows what Drive actually holds under `photos_root`, grouped
by month, from the `drive_files` table. Organize keeps it current on its own:
it records each newly uploaded file the moment it lands, and a file it adopts
instead of uploading was already indexed by an earlier Scan — so there is
nothing to re-run just to see the result of a Sync. Re-run Scan (or
Reorganize Folders' Index phase) to pick up anything that changed in Drive
some other way. Thumbnails come from Drive's own renderer through a local disk
cache in `.cache/thumbnails` — Chrome cannot display HEIC, and 591 of these
files are HEIC. Videos play in Drive's embedded preview, which handles the
HEVC `.MOV` files browsers refuse.

Filter by month, country, media type, tag, or duplicate status, and
combine them freely. Duplicates are a filter here, not a separate page: those
files were uploaded anyway, so they are part of the library. Select with
click, shift-click for a range, ⌘-click to toggle, or **Select all matching
this filter**, which selects the entire result set rather than only the tiles
on screen. Double-click opens one file without disturbing the selection.

Tags are yours to invent — `family`, `greece-2025`, `print-these`. Month,
country and media type are *not* tags: they are filters derived from data
the catalog already holds, so there is nothing to regenerate and nothing to
go stale. Tagging writes only to the local catalog, which is why it is
instant.

Reorganize Folders' Enrich phase fills the catalog in from the other
direction: for every file it has not looked at yet, it reads Drive's own
EXIF capture time and coordinates and any `t_*` appProperties already on the
file. For a file that came from an archive this mostly confirms what Plan
Organization and Sync Tags already resolved; for anything added to the
Global Photos folder some other way, it is the only source of a date,
country or tag at all. It only ever adds tags this way, never removes one —
so reading appProperties back into the catalog means losing the catalog no
longer means losing your tags.

**Sync Tags to Drive** is what makes tags durable. It compares each file's
`t_*` appProperties against the catalog, reports every add and removal, and
changes nothing until you re-run it with `confirm`. Tags then travel with the
file and survive the loss of this machine. Drive allows 30 properties per
file and Organize already uses about five, so a file carrying more than 25
tags is reported and skipped rather than failing obscurely.

## Tests

```bash
uv run pytest              # backend, offline, no network
uv run pytest -m live      # opt-in, hits the real Drive account
cd web && npm test         # frontend
```

The default backend suite never touches the network. Drive behaviour is covered
by `tests/fakes/fake_drive.py`, and ZIP behaviour by archives built in memory.

Paths resolve relative to the repo root. When working from a git worktree, the
credentials live in the main checkout, so point the live suite at it:
`PHOTOLIB_HOME=/path/to/main/checkout uv run pytest -m live`.

## Architecture

- `photolib/drive/` — OAuth token refresh and a REST client over `httpx`
- `photolib/ziparchive/` — reads ZIP indexes and extracts single entries using
  HTTP byte ranges, so a 2.15 GB archive is never downloaded to retrieve one photo
- `photolib/db/` — SQLite catalog holding settings, archive indexes, and jobs
- `photolib/thumbs.py` — disk-cached proxy for Drive's thumbnail renders
- `photolib/downloads.py` — the per-run download folder and the live transfer
  registry behind `GET /api/downloads`
- `photolib/actions/` — one module per capability; each becomes a page in the UI
- `photolib/jobs/` — a background worker that runs actions and streams progress
- `photolib/api/` — FastAPI routes
- `web/` — React + Vite frontend

## Adding an action

Create a module in `photolib/actions/` declaring `ID`, `TITLE`, `DESCRIPTION`,
`ORDER`, a `Params` model extending `ActionParams`, and a `run(ctx, params)` generator that
yields `ProgressEvent`s. The registry discovers it automatically and the
frontend renders a page for it — no frontend changes required. An optional
`GROUP` attribute, `"flow"` or `"advanced"`, decides which section of the
sidebar it lands in; it defaults to `"advanced"`, so a new action has to opt
in to being one of the two flows. `ORDER` sorts within a group, not across
both.

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

## The pipeline

Run these in order from the UI.

| Action | Does | Writes to Drive |
| --- | --- | --- |
| Check Connection | Verifies credentials and folders | No |
| Scan Archives | Indexes archive contents and the destination folder | No |
| Pair Metadata | Matches sidecars to media across archive parts | No |
| Plan Organization | Resolves dates, countries, duplicates, destinations | No |
| Review Plan | Shows every file and where it would go | No |
| **Organize Photos** | **Uploads every planned file into its destination bucket folder** | **Yes** |
| **Reorganize Folders** | **Repacks existing files into ~100-file bucket folders, trashing folders left empty** | **Yes** |
| Library | Browse, filter, and tag what is now in Drive | No |
| Tags | Create, rename, merge, and delete tags | No |
| **Sync Tags to Drive** | **Mirrors tags onto each file's `appProperties`** | **Yes** |
| **Clear Stale Trees** | **Moves a redundant extracted tree to Drive's trash** | **Yes** |

Everything unbolded is safe to repeat. The four bolded rows mutate Drive.

Organised photos are destined for whole-month bucket folders under `Photos/`,
packed greedily to roughly 100 files each: a busy month gets its own folder
(`2026-05`), quiet months share one (`2025-01 - 2025-03`), and files with no
resolvable date land in `unknown-date`. Because the packing depends on how
many files a month ends up holding, later uploads can shift where a month's
bucket falls — **Reorganize Folders** repacks the existing files to match,
report-then-confirm like Sync Tags, with metadata-only moves (no bytes are
re-downloaded or re-uploaded) and folders left empty trashed once they clear.
The existing `back_*` folders are indexed for duplicate detection and are
never read from, written to, renamed, or moved.

Files that already exist in the destination are flagged but **still uploaded** —
deduplication is a deliberate later step, not part of this pipeline.

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

## Clearing the stale trees

`Clear Stale Trees` takes the Drive folder id of one extracted tree and reports
what it would trash without changing anything. A file is eligible only when a
file of the same name has already been uploaded **and** verified against Drive's
own MD5. Re-run with `confirm` to act. It moves files to Drive's trash, where
they stay recoverable; nothing is permanently deleted and the source archives
are never touched.

## Browsing and tagging

The Library page shows what Drive actually holds under `photos_root`, grouped
by month. It is built from the last Scan, so re-run Scan after an Organize to
see new files. Thumbnails come from Drive's own renderer through a local disk
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
frontend renders a page for it — no frontend changes required.

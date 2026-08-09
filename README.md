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

Paths resolve relative to the repo root. When working from a git worktree, the
credentials live in the main checkout, so point the live suite at it:
`PHOTOLIB_HOME=/path/to/main/checkout uv run pytest -m live`.

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

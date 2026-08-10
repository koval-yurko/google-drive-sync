# Visible download folder — design

**Date:** 2026-08-10
**Status:** approved, ready for planning

## Problem

Organize already spools every file to disk before uploading it, at
`.cache/spool`, hardcoded in `photolib/actions/organize.py`. You cannot watch
it work. The folder is hidden, its filenames are `tmpXXXXXX.part`, and the
whole directory is deleted at the start and end of every run — so a crashed
run leaves no trace, and a running one tells you nothing about which photo is
moving.

The goal is to see in-progress files: on disk, and in the UI while a run is
going.

## Scope

In scope: a visible, per-run download folder; readable filenames; an in-flight
panel on the Organize page.

Out of scope, deliberately:

- **A configurable download path.** The folder is `downloads/` at the repo
  root. No setting, no environment variable.
- **Keeping files after upload.** A spooled file is still unlinked the moment
  its upload verifies. The folder shows work in progress, not an archive.

## 1. The folder

`Config` gains one field:

```python
downloads_dir: Path      # root / "downloads"
```

`downloads/` is added to `.gitignore`.

Each Organize run creates its own subfolder, named for the local time the run
started, in a sortable, filesystem-safe form:

```
downloads/2026-08-10_14-32-05/
```

A run touches only its own folder. This is the whole reason for the per-run
subfolder: the previous behaviour swept the shared spool clean on start, so a
crash was indistinguishable from a clean exit.

Lifecycle rules:

- **On start:** create this run's folder. Scan `downloads/` for folders left by
  earlier runs. Delete the empty ones — they carry no information. Leave the
  non-empty ones alone and emit one job-log line naming them and their total
  size, e.g. *"2 earlier runs left 1.4 GB in `downloads/`."*
- **On finish:** remove this run's folder. It is empty by then, because
  `transfer_entry` unlinks each spooled file in its `finally` block. If it is
  not empty, leave it and log it — an unexpected leftover is a fact worth
  keeping, not one worth deleting.
- **Never:** delete another run's non-empty folder. That is the user's call.

The timestamp comes from `datetime.now()` called once at the top of the run and
passed down, so a test can inject a fixed value rather than monkeypatching a
module global.

## 2. Readable filenames

`transfer_entry` currently does:

```python
handle, raw_path = tempfile.mkstemp(dir=spool_dir, suffix=".part")
```

It instead derives the name from the file being moved:

```
downloads/2026-08-10_14-32-05/IMG_1234.HEIC.part
```

Uniqueness matters because the folder is flat and workers run concurrently:
two entries can share a `target_name` when they land in different months. The
name is claimed atomically with `os.open(path, O_CREAT | O_EXCL | O_WRONLY)`.
On `FileExistsError` the next candidate is tried:

```
IMG_1234.HEIC.part → IMG_1234.HEIC.2.part → IMG_1234.HEIC.3.part → ...
```

The loop is bounded; exhausting it raises `TransferError(stage="read")` rather
than looping forever. The suffix stays `.part` so the name still reads as
unfinished.

`transfer_entry` keeps its `spool_dir` parameter — it is handed the run folder,
not the root — so its contract is unchanged and its tests stay honest.

## 3. The in-flight panel

### Registry

A new `photolib/jobs/inflight.py` holds a small thread-safe registry. It is
created once in `create_app`, stored on `app.state.inflight`, and passed into
`ActionContext` so an action can reach it:

```python
class InflightRegistry:
    def start(self, key: str, *, name: str, destination: str,
              expected_size: int, path: Path) -> None: ...
    def uploaded(self, key: str, offset: int) -> None: ...
    def finish(self, key: str) -> None: ...
    def snapshot(self) -> list[Transfer]: ...
```

`key` is the entry id. `ActionContext.inflight` defaults to `None`, like
`writer` does, so the many contexts that never transfer anything — and every
existing test that builds one — need no change. All mutation is under one lock; `snapshot` returns
plain copies, so a reader never sees a half-updated record. The registry holds
no history: a finished transfer is gone from it.

Organize registers a transfer before calling `transfer_entry`, passes
`on_progress=lambda offset: ctx.inflight.uploaded(key, offset)` — a hook
`transfer_entry` already accepts and nothing currently uses — and calls
`finish` in a `finally`, so a failure cannot leave a ghost row.

### Endpoint

`GET /api/downloads` merges the registry with the truth on disk:

```json
{
  "run_dir": "downloads/2026-08-10_14-32-05",
  "files": [
    {"name": "IMG_1234.HEIC", "phase": "downloading",
     "bytes": 1258291, "total": 3565158, "destination": "Photos/2025-07"},
    {"name": "IMG_1240.MOV", "phase": "uploading",
     "bytes": 18874368, "total": 44145213, "destination": "Photos/2025-08"}
  ],
  "stale_runs": [{"dir": "2026-08-09_22-14-01", "files": 3, "bytes": 1503238553}]
}
```

Per file:

- `phase` is `downloading` until the bytes on disk reach `expected_size`, then
  `uploading`.
- `bytes` is `stat().st_size` of the `.part` file while downloading, and the
  registry's uploaded offset while uploading.
- A `.part` file whose `stat` fails because it was just unlinked is dropped
  from the response rather than erroring — the race is expected and harmless.

`stale_runs` is computed by scanning `downloads/` for directories other than
the active run's. When no run is active, `run_dir` is `null` and `files` is
empty; `stale_runs` is still reported.

### UI

`ActionPage` renders the panel under `JobProgress` when the action is
`organize` and a job is running, polling `/api/downloads` once a second and
stopping when the job ends. One row per file:

```
IMG_1234.HEIC   downloading  ███░░░░░░░  1.2 / 3.4 MB   → Photos/2025-07
IMG_1240.MOV    uploading    ████████░░ 18.0 / 42.1 MB  → Photos/2025-08
```

An empty list renders nothing rather than an empty frame. A non-empty
`stale_runs` renders one line above the table naming the folder and its size.

## Data flow

```
ZIP in Drive ──range reads──> downloads/<run>/IMG_1234.HEIC.part ──> Drive
                    │                    │                  │
              registry.start       stat() = bytes    registry.uploaded()
                    └──────────────> GET /api/downloads <───────┘
                                            │
                                     Organize page, 1 Hz
```

## Error handling

| Situation | Behaviour |
| --- | --- |
| Name collision inside a run | Atomic `O_EXCL` claim, numeric suffix, bounded retries |
| Run crashes mid-transfer | Its folder and `.part` files survive, named for the run; next run reports them, deletes nothing |
| `.part` unlinked during a poll | That file is omitted from the response |
| Registry entry left by a failure | Impossible: `finish` runs in a `finally` |
| `downloads/` unwritable | Organize fails at start with a plain error event, before any Drive session opens |

## Testing

Backend, offline:

- Naming: two entries with the same `target_name` in one folder produce
  `X.part` and `X.2.part`; the bounded retry raises rather than spinning.
- Registry: phase flips at `expected_size`; `finish` removes the record;
  concurrent `uploaded` calls from threads leave a consistent snapshot.
- Organize: creates a folder named for the injected timestamp; removes it on
  success; prunes an empty foreign run folder; leaves a non-empty one and logs
  it. The existing `test_the_spool_directory_is_left_empty` becomes
  `test_the_run_folder_is_removed`.
- Endpoint: over a fabricated run folder plus a seeded registry, returns both
  phases, correct byte counts, and the stale-run summary; returns an empty
  payload when nothing is running.

Frontend:

- The table renders both phases with correct progress widths, shows the stale
  line when present, and renders nothing when the file list is empty.

## Files touched

| File | Change |
| --- | --- |
| `photolib/config.py` | `downloads_dir` field |
| `photolib/transfer.py` | Named `.part` claim replacing `mkstemp` |
| `photolib/actions/organize.py` | Per-run folder, lifecycle, registry wiring |
| `photolib/actions/base.py` | `inflight` on `ActionContext` |
| `photolib/jobs/inflight.py` | New — the registry |
| `photolib/api/routes_downloads.py` | New — `GET /api/downloads` |
| `photolib/api/app.py` | Construct and share the registry; mount the route |
| `web/src/api/client.ts`, `types.ts` | `getDownloads` |
| `web/src/pages/ActionPage.tsx` | Panel, gated on `organize` + running |
| `web/src/components/InflightTable.tsx` | New — the table |
| `.gitignore`, `README.md` | `downloads/`, and a paragraph on watching a run |

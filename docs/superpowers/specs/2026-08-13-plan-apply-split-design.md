# Split planning from execution

**Date:** 2026-08-13
**Status:** Approved, not yet implemented

## The problem

`photolib/` has eleven modules sitting loose at the top level with no package:
`archives`, `buckets`, `dedupe`, `downloads`, `enrich`, `places`, `repack`,
`scan`, `takeout`, `thumbs`, `transfer`. The tree does not say what any of them
are, so "where does this new function go?" has no mechanical answer.

The loose modules are not the whole problem. The mixing is *inside* them.
`repack.py` (242 lines) holds `_histogram(conn)` and `targets_for(conn)` doing
SQL, `moves_from_targets()` doing pure computation, and `apply_move(writer)`,
`ensure_folders(writer)` and `apply_sweep(writer)` mutating Drive — three
layers in one file. `dedupe.py` and `buckets.py` do the same at smaller scale.
Grouping these files into folders would file mixed-concern modules under a
single honest-sounding label without making anything clearer.

Two related defects fall out of the same tangle:

- Raw SQL appears in eight modules outside `db/` — `places`, `dedupe`,
  `buckets`, `repack`, `actions/verify_library`, `actions/sync_tags`,
  `actions/steps/plan_organize`, `api/routes_review` — so the repository
  pattern is bypassed more often than the tree suggests.
- `plan_moves(drive, conn, root_id, *, exclude)` uses neither `drive` nor
  `root_id`. Every one of its seven call sites passes them anyway; six are
  tests constructing a `FakeDrive()` for a parameter that is never read.

## The rule

> **Planning takes readers** (`drive`, `conn`) **and returns a value.**
> **Execution takes a `writer` and returns `None`.**

Every function being moved already obeys this; it is simply invisible in the
tree. `plan_sweep(drive, root_id)` reads Drive and returns a list;
`apply_sweep(writer, folder_id)` mutates. Making the rule structural turns
"where does this go?" into a one-second look at the signature.

Note that a Drive *read* is planning. The axis is mutation, not I/O.

## Target structure

```
photolib/
  planning/          decides what should happen — readers in, plan out
    takeout.py       ← takeout.py                    (unchanged, already pure)
    buckets.py       ← buckets.py: month_of, Bucket, pack, folder_map
    layout.py        ← repack.py: Move, targets_for, moves_from_targets,
                                  plan_moves, plan_sweep, folder_paths
    duplicates.py    ← dedupe.py: Removal, _walk, plan_removals
    enrich.py        ← enrich.py                     (unchanged)

  execution/         enacts it — writer in, nothing out
    transfer.py      ← transfer.py                   (unchanged)
    moves.py         ← repack.py: apply_move, ensure_folders, apply_sweep
    trash.py         ← dedupe.py: apply_removal
    downloads.py     ← downloads.py    the folder execution writes into,
                                       plus the registry it reports to

  db/          + layout_repo.py  geocache_repo.py
  drive/       + thumbs.py       ← thumbs.py    (a disk cache over Drive's renderer)
  ziparchive/  + source.py       ← archives.py  (the Drive↔ZIP bridge)

  actions/  api/  jobs/                         unchanged
  config.py  places.py  ingest.py ← scan.py     deliberate top-level modules
```

Two new packages rather than three. `thumbs.py` and `archives.py` are adapters
for subsystems that already have a package, so folding them in avoids inventing
a grab-bag `misc/`.

### Why three modules stay at the top level

- **`config.py`** — path and environment resolution. Belongs above everything.
- **`places.py`** — an HTTP geocoder with a persistent cache. An external
  service adapter; it is not planning, and it has no natural host package.
  Putting it in `planning/` would place an HTTP client in the package whose
  defining property is that it does not reach the network.
- **`ingest.py`** (renamed from `scan.py`) — `index_destination(drive, conn,
  folder_id)` reads Drive and writes the catalog. It is neither plan nor apply.
  Folding it into `ScanRepo`, the option first considered, would give a
  persistence class a Drive dependency — a new `db → drive` edge in a refactor
  whose point is clean layering, and the same shape as the `db/scan_repo.py →
  ziparchive` leak being fixed here. It stays a module; the rename says what it
  does.

## Repository methods

The raw SQL in those eight modules moves behind repos — fifteen methods, since
several modules carry more than one query. Names below were checked against the
existing repo surfaces; none collide.

| Current site | New home |
|---|---|
| `buckets.library_histogram`, `buckets.unaccounted_drive_months`, `repack._histogram` | `LayoutRepo.capture_histogram(exclude)` |
| `repack.FOLDER_QUERY` via `targets_for` | `LayoutRepo.live_files_for_layout()` |
| `repack.apply_move`'s two `UPDATE`s | `LayoutRepo.record_move(...)` |
| `dedupe.plan_removals`' verified-id set | `MediaRepo.uploaded_drive_ids()` |
| `dedupe.apply_removal`'s `UPDATE` | `ScanRepo.mark_trashed(drive_id, when)` |
| `verify_library`'s uploaded-media query | `MediaRepo.uploaded_with_names()` |
| `verify_library`'s orphan-tags query | `TagsRepo.orphaned_drive_ids()` |
| `sync_tags`' pending select | `TagsRepo.pending_sync(limit)` |
| `sync_tags`' `synced_tags` update | `TagsRepo.mark_synced(drive_id, tags)` |
| `plan_organize`'s sidecar lookup | `MediaRepo.sidecar(sidecar_id)` |
| `plan_organize`'s archive-modified lookup | `ScanRepo.archive_modified_time(drive_id)` |
| `routes_review`'s filtered count + page | `MediaRepo.review_page(filters, limit, offset)` |
| `routes_review`'s existence check | `MediaRepo.exists(entry_id)` |
| `places.Geocoder`'s geocache read/write | `GeocacheRepo.get(key)` / `.put(key, country, payload)` |

`LayoutRepo` is new because the layout queries span `drive_files` and `media`
and belong to neither `ScanRepo` nor `MediaRepo` alone. It is named after its
consumer, `planning/layout.py`.

`GeocacheRepo` is new and has a second benefit: `Geocoder` takes the repo
instead of a raw connection, so it becomes testable without SQLite.

`LayoutRepo.record_move` collapses `apply_move`'s two `UPDATE` statements and
one `commit` into a single transaction. Today a crash between the two
statements leaves `drive_files` and `media` disagreeing about where a file
lives; after this they land together or not at all.

## Enforcement

An `import-linter` layers contract in CI:

```
api → actions → execution → planning → db | drive | ziparchive → config
```

plus explicit `forbidden` contracts for:

- `planning → execution` — the whole point of the split.
- `db → ziparchive` — `db/scan_repo.py` currently imports `ziparchive.reader.ZipEntry`.
- `planning → drive.client` — `enrich.py` currently imports `drive.client.DriveFile`.

The last two are pre-existing leaks, not new constraints. Satisfying them needs
a small `Protocol` or a local dataclass on each side of the boundary so the
consumer names a shape rather than a concrete transport type.

## Sequence

Six steps. Each leaves the suite green, so every step is independently
reviewable and revertible.

1. **Add repos.** The fifteen methods in the table above, plus the two new
   repos that host some of them. Pure addition; nothing calls them yet.
2. **Repoint the SQL.** Switch the eight modules to the new methods; delete the
   inline queries.
3. **Split the packages.** Create `planning/` and `execution/`; move and split
   `repack`, `dedupe`, `buckets`, and move the unchanged modules; rewrite
   imports.
4. **Absorb the adapters.** `archives.py` → `ziparchive/source.py`,
   `thumbs.py` → `drive/thumbs.py`, `scan.py` → `ingest.py`.
5. **Drop the dead parameters.** Remove `plan_moves`' `drive` and `root_id`;
   update seven call sites, six of them tests.
6. **Add the contract.** `import-linter` config, CI wiring, and the two
   `Protocol`s the forbidden contracts force.

## Tests

`test_repack.py` and `test_dedupe.py` split to follow their modules:

- `test_repack.py` → `test_planning_layout.py` + `test_execution_moves.py`
- `test_dedupe.py` → `test_planning_duplicates.py` + `test_execution_trash.py`

The other 52 test files stay where they are and change only their imports.

Splitting `tests/` into `unit/` and `integration/` directories is explicitly
**out of scope**. It is a separate decision with its own trade-offs, and
bundling it here would make the diff harder to review without serving the goal.

## Risk

Step 3 is the only step where a mistake is quiet rather than loud.

`repack._histogram` and `dedupe.plan_removals` each hold `with conn.lock:`
around a cursor they iterate rather than materialise. `catalog.LockedConnection`
documents why: `execute` releases the lock once the statement is prepared, so
rows fetched afterwards are unprotected, and a read whose two halves must
describe one state of the catalog has to hold the lock itself. Moving those
bodies into `LayoutRepo` and `MediaRepo` must preserve that, and a mistake
produces a rare interleaving bug rather than a test failure. Both migrations
get specific review attention.

Everything else in the change is import rewrites and function moves, which the
existing 8,751 lines of tests cover.

## Out of scope

Recorded so they are not silently dropped:

- `src/` layout, and the `schema.sql` `force-include` it would simplify.
- Replacing `app.state` service-locator access in routes with `Depends()`
  providers.
- `Protocol` types for `ActionContext.drive` / `.writer` / `.inflight`, and the
  `hasattr(drive_client, "start_session")` feature check in `api/app.py` that
  stands in for them.
- Tooling config: ruff, a formatter, a type checker, coverage. Highest
  benefit-to-effort item in the codebase, but orthogonal to organization.
- Moving runtime state (`photolib.db`, `downloads/`, `.cache/`, `token.json`)
  out of the repo root.
- `unit/` vs `integration/` test directories.

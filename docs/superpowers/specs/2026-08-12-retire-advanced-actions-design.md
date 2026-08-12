# Retire the Advanced actions; keep only what the flows need

Date: 2026-08-12. Status: approved.

## Problem

The sidebar carries an `Advanced` disclosure listing ten actions — Clear Stale
Trees, Repack Buckets, Organize Photos, Plan Organization, Pair Metadata, Scan
Archives, Check Connection, Clear Duplicates, Sync Tags to Drive, Verify
Library. The operator's question was blunt: the two flows, **Sync from
Archives** and **Reorganize Folders**, are supposed to do all of this — so why
are these still here?

They are there by accident. `registry._discover` turns any module in
`photolib/actions/` declaring `ID`, `TITLE`, `DESCRIPTION`, `ORDER`, `Params`
and `run` into an action, and therefore a page. Being importable is enough to
get a nav entry, so implementation details are published as UI.

But "delete Advanced" would break the app, because the ten entries are four
different things wearing one label. An audit against both flows found:

- **Five are literally Sync from Archives' implementation.**
  `sync_archives.py:13-19` imports `check_connection`, `scan_archives`,
  `pair_metadata`, `plan_organize` and `organize` and drives each one's
  `module.run` / `module.Params()` through `run_phase`. Deleting a module
  deletes a phase of the flow.
- **Two duplicate flow phases** over the same core modules: Repack Buckets
  (`reorganize.py` → `photolib.repack`) and Clear Duplicates
  (→ `photolib.dedupe`).
- **Three are in no flow at all** — Sync Tags to Drive, Clear Stale Trees,
  Verify Library. For these, Advanced is the only way to run them.

One of the duplicates is worse than redundant. Standalone Repack Buckets calls
`repack.targets_for(ctx.conn)` with no exclusions, while the flow calls
`repack.plan_moves(..., exclude=doomed)`. That `exclude` is deliberate —
`reorganize_library.py:9-10`: *"Dedupe runs before Repack deliberately: a file
about to be trashed must not reserve space in a bucket, or every dedupe would
leave the layout wrong."* Running the standalone version reserves bucket space
for files that are about to be trashed, producing exactly the layout the flow
exists to avoid.

## Decisions (made with the operator)

Every action lands in exactly one bucket:

| Action | Fate |
| --- | --- |
| `check_connection`, `scan_archives`, `pair_metadata`, `plan_organize`, `organize` | move to `photolib/actions/steps/`, stop being actions |
| `sync_tags` | fold into Reorganize Folders as a sixth phase |
| `clear_stale_trees` | delete |
| `reorganize` (Repack Buckets), `clear_duplicates` | delete |
| `verify_library` | keep, regrouped as `GROUP = "tool"` |

Rationale for the two judgement calls:

- **Clear Stale Trees is deleted, not folded.** It is a one-time migration
  cleanup — it trashes a hand-extracted Takeout tree once its files are safely
  in the Global folder — and the operator confirmed the trees are already
  cleared. It is also the weakest matcher in the codebase: eligibility comes
  from `MediaRepo.uploaded_by_name()`, keyed on **filename alone**, where the
  rest of the project uses `(crc32, size)`. A tree holding a 2019
  `IMG_1234.HEIC` would be judged eligible because a *different* 2023
  `IMG_1234.HEIC` was uploaded. Keeping a rarely-needed destructive action
  with that matcher is worse than deleting it; Drive's own UI can delete a
  folder if one ever reappears.
- **Verify Library stays standalone.** It is read-only and diagnostic — the
  thing you run when something looks wrong — not a step of a normal run.
  Folding it into Reorganize Folders would mean running a whole reorganize
  just to verify.

The resulting sidebar:

```
Flows     Sync from Archives · Reorganize Folders
Browse    Library · Tags
Activity  Review Plan · Jobs
Tools     Verify Library
```

## Design

### Registry: 12 actions become 3

`registry._discover` uses `pkgutil.iter_modules(photolib.actions.__path__)`,
which is **non-recursive**. A subpackage is therefore undiscovered without any
change to the registry: `iter_modules` yields `steps` itself, `import_module`
loads its `__init__.py`, that has none of the six required attributes, and the
loop skips it. The submodules are never visited.

```
photolib/actions/
  sync_archives.py       flow   Sync from Archives
  reorganize_library.py  flow   Reorganize Folders
  verify_library.py      tool   Verify Library
  base.py  registry.py  phases.py
  steps/
    __init__.py
    check_connection.py  scan_archives.py  pair_metadata.py
    plan_organize.py     organize.py
```

This was chosen over two alternatives. Dropping each module's `ID`/`TITLE`/
`DESCRIPTION`/`ORDER` leaves the intent expressed only as the *absence* of four
attributes, which someone re-adds by accident. A `GROUP = "internal"` value
keeps them registered and runnable via `POST /api/actions/{id}/run` and still
present in the `/api/actions` payload, moving the clutter rather than removing
it. The directory name states the intent structurally.

`ActionSpec.group` keeps defaulting to `"advanced"`, but after this change no
module relies on the default: the two flows declare `"flow"` and
`verify_library` declares `"tool"`.

### Reorganize Folders gains a `tags` phase

```python
PHASES = ("index", "enrich", "dedupe", "repack", "sweep", "tags")
```

Last, because tags are metadata: if the Drive property writes fail, the library
is still correctly deduplicated and repacked. `_LABELS` re-slices its spans
across six phases. `PHASES` is consumed only by `len()` in `_label`
(`reorganize_library.py:54`), so nothing else shifts.

**The fold closes a deferred performance item.** `sync_tags.py:95` issues one
`drive.app_properties(drive_id)` GET per candidate. The
`2026-08-12-post-merge-correctness-fixes` plan listed fixing this under
"Larger, needs its own design":

> *"Caching them was considered and rejected during the merge: `sync_tags` is
> reachable standalone with no enforced preceding Index, so a stored snapshot
> could be arbitrarily stale and would break its documented promise to read
> Drive for the diff. Making it safe needs a freshness policy, not a cache."*

Folding dissolves that objection rather than solving it. As a phase, sync_tags
is no longer reachable standalone, and Index always runs moments earlier in the
same flow. The phase reads `walked_by_id` — already built at
`reorganize_library.py:124` for Enrich — so the walk *is* the fresh read, with
the same `get_file` fallback Enrich uses for a row the walk did not produce.
No freshness policy is needed because there is no stale snapshot.

Preserved from the standalone action:

- the confirm gate (dry run reports; `confirm` acts),
- the `MAX_TAGS = 25` budget check, which refuses a file whose tag count will
  not fit Drive's appProperties,
- the candidate query — files tagged now **or** carrying `synced_tags` from
  last time, so untagging a file still removes its property rather than
  dropping it out of the set.

`sync_tags.PREFIX = "t_"` is deleted in favour of the existing
`enrich.TAG_PREFIX = "t_"` (`enrich.py:15`), collapsing a duplicated constant.

### A knock-on: `uploaded_by_name` becomes dead code

`clear_stale_trees.py:89` is the **only** production caller of
`MediaRepo.uploaded_by_name()` (`media_repo.py:303`); the sole other reference
is its own test at `test_media_repo_uploads.py:95`. Deleting the action leaves
the method unreachable.

It is deleted along with its test. This also settles a note left open by the
`2026-08-12-post-merge-correctness-fixes` plan, which recorded under
"Duplication and naming" that *"`verified_by_crc` duplicates
`uploaded_by_name`'s query"* — with `uploaded_by_name` gone, the two queries
become one.

`verified_by_crc` is untouched: it is live, keyed on `(crc32, size)`, and used
by Plan's skip verdict.

### Error handling

Unchanged in kind. The tags phase adopts the per-item guard the Enrich and
Repack phases already use: one file's `DriveError` records a `failed` row in
`job_items` and the flow continues, rather than failing before later phases
run. The dry run writes intended operations to `job_items`; `confirm` reads
them back and marks each done, so a killed confirm resumes instead of
re-planning.

**To verify during implementation, not assume:** a `run_id` from a job started
before this change has no `tags` rows in `job_items`. Resume must treat a
missing phase as "not yet done" rather than "nothing to do".

### Testing

48 tests live in the six affected files:

| File | Tests | Disposition |
| --- | --- | --- |
| `test_action_sync_tags.py` | 14 | rewritten against the flow's tags phase |
| `test_action_reorganize.py` | 14 | audited; unique coverage ported, then deleted |
| `test_action_clear_dupes.py` | 8 | audited; unique coverage ported, then deleted |
| `test_action_clear_stale.py` | 7 | deleted with the action |
| `test_action_clear_dupes_summary.py` | 3 | audited; unique coverage ported, then deleted |
| `test_action_reorganize_stale_parent.py` | 2 | re-pointed at `reorganize_library` |

Plus one test outside those files: `test_media_repo_uploads.py:95` goes with
`uploaded_by_name`.

The risk is deleting unique coverage, so the sequence is **port first, delete
second** — never the reverse. The stale-parent case in particular is real
behaviour, not an artifact of the wrapper, and must survive.

New tests:

- modules under `steps/` are not discovered as actions,
- the registry contains exactly `sync_archives`, `reorganize_library`,
  `verify_library`,
- `Nav` renders a Tools section and no Advanced disclosure.

`tests/test_actions.py:22` asserts `"check_connection" in ids` directly from
the registry. That assertion inverts, and is the tripwire proving the demotion
took effect.

### Docs and UI

`Nav.tsx:33-42` replaces the `<details><summary>Advanced</summary>` block with
a `Tools` section filtering `group === 'tool'`. README drops its "Clearing the
stale trees" section and the table rows for the deleted actions.

## Delivery: two plans

**Plan A — retire and demote.** Delete the three actions, move the five phase
modules into `steps/`, regroup the nav, update docs. Mechanical and low risk.

**Plan B — fold Sync Tags.** Add the sixth phase to Reorganize Folders, reusing
the Index walk, and delete `sync_tags.py`. A real logic change touching a flow
with resume semantics.

Split so a bisect can distinguish "the move broke it" from "the fold broke it".
Plan A must land first: Plan B assumes the `steps/` layout.

## Out of scope

- Any change to what the two flows *do*, beyond gaining the tags phase.
- Strengthening `uploaded_by_name()` to `(crc32, size)` — moot once
  `clear_stale_trees` is deleted, since the method is deleted with it.
- The `ActionSpec.group` default of `"advanced"`. No module relies on it after
  this change, but removing the default is a separate cleanup.
- Merging `verify_library` into a flow.

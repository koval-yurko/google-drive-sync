# Retire the Advanced Actions (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the action registry from 12 entries to 3 — two flows plus one diagnostic — by deleting three redundant actions and demoting the five that are really Sync from Archives' internal phases.

**Architecture:** Three deletions, one package move, one regrouping. `registry._discover` uses `pkgutil.iter_modules`, which is non-recursive, so moving the five phase modules into `photolib/actions/steps/` de-registers them without touching the registry at all. Nothing about what the two flows *do* changes in this plan.

**Tech Stack:** Python 3.12, FastAPI, SQLite (`sqlite3` stdlib), pytest; React 18 + Vite + TypeScript + vitest.

## Global Constraints

- Python 3.12, managed by `uv`. Backend tests run with `uv run pytest`.
- The default backend suite **never touches the network.** Drive is faked by `tests/fakes/fake_drive.py`; ZIPs are built in memory by `tests/fixtures/zipbuilder.py`.
- Destructive Drive operations **trash**, never delete.
- Frontend tests: `cd web && npm test`. Type check: `cd web && npx tsc -b`.
- **Baseline: backend 628 passed, 14 deselected; frontend 156 passed.** Every task states its own expected count; if the actual differs, stop and report rather than adjusting the number.
- Never run `git stash` or any stash subcommand — the stash stack is shared with other checkouts. Use a temporary WIP commit instead.
- **Port before you delete, never the reverse.** Several tasks remove test files. In each case the porting step comes first and is verified green before anything is deleted.
- Run every command from the repo root. `cd web` for frontend commands, then `cd ..` back.

## Context

This plan implements Plan A of `docs/superpowers/specs/2026-08-12-retire-advanced-actions-design.md`. Read that spec first — it records why each action lives or dies.

The ten `Advanced` entries are four different things wearing one label:

| Bucket | Actions | This plan |
| --- | --- | --- |
| Sync from Archives' own phases | `check_connection`, `scan_archives`, `pair_metadata`, `plan_organize`, `organize` | move to `steps/` |
| Duplicate a flow phase | `reorganize` (Repack Buckets), `clear_duplicates` | delete |
| One-time migration cleanup | `clear_stale_trees` | delete |
| Standalone diagnostic | `verify_library` | keep, regroup as `tool` |
| Folded into Reorganize Folders | `sync_tags` | **Plan B, not here** |

`sync_tags` is untouched by this plan. It keeps working exactly as it does today and stays in the sidebar until Plan B lands.

## File Structure

| File | Change |
| --- | --- |
| `photolib/actions/clear_stale_trees.py` | delete |
| `photolib/actions/clear_duplicates.py` | delete |
| `photolib/actions/reorganize.py` | delete |
| `photolib/db/media_repo.py` | delete `uploaded_by_name` (no caller survives) |
| `photolib/dedupe.py` | drop `apply_removal`'s now-unused `stamp` parameter |
| `photolib/actions/steps/__init__.py` | create (empty; its existence is what de-registers the modules) |
| `photolib/actions/steps/{check_connection,scan_archives,pair_metadata,plan_organize,organize}.py` | moved from `photolib/actions/` |
| `photolib/actions/sync_archives.py` | import the five from `.steps` |
| `photolib/actions/verify_library.py` | add `GROUP = "tool"` |
| `web/src/components/Nav.tsx` | `Advanced` disclosure → `Tools` section |
| `web/src/api/types.ts` | `group: 'flow' \| 'advanced'` → `'flow' \| 'tool'` |
| `README.md` | drop three table rows and the stale-trees section |
| ~12 test files | import updates, fixture re-pointing, deletions |

---

### Task 1: Delete Clear Stale Trees and the dead code it was propping up

**Files:**
- Delete: `photolib/actions/clear_stale_trees.py`
- Delete: `tests/test_action_clear_stale.py`
- Modify: `photolib/db/media_repo.py` (remove `uploaded_by_name`)
- Modify: `tests/test_media_repo_uploads.py` (remove its test)
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `MediaRepo.uploaded_by_name` ceases to exist; no later task may reference it. `MediaRepo.verified_by_crc` is untouched and stays the `(crc32, size)` lookup Plan's skip verdict uses.

**Why this matters.** Clear Stale Trees trashes a hand-extracted Takeout tree once its files are safely in the Global folder — a one-time migration cleanup the operator has already completed. It is also the weakest matcher in the codebase: eligibility comes from `MediaRepo.uploaded_by_name()`, keyed on **filename alone**, where the rest of the project uses `(crc32, size)`. A tree holding a 2019 `IMG_1234.HEIC` would be judged eligible because a *different* 2023 `IMG_1234.HEIC` was uploaded.

`clear_stale_trees.py:89` is the only production caller of `uploaded_by_name`, so the method dies with it. That also settles a note the `2026-08-12-post-merge-correctness-fixes` plan left open: *"`verified_by_crc` duplicates `uploaded_by_name`'s query."*

- [ ] **Step 1: Prove the claim before acting on it**

Run:
```bash
grep -rn "clear_stale_trees" --include="*.py" photolib tests
grep -rn "uploaded_by_name" --include="*.py" photolib tests
```

Expected: `clear_stale_trees` appears only in `photolib/actions/clear_stale_trees.py` and `tests/test_action_clear_stale.py`. `uploaded_by_name` appears only in `photolib/actions/clear_stale_trees.py:89`, its definition in `photolib/db/media_repo.py`, and `tests/test_media_repo_uploads.py`.

If anything else references either, **stop and report** — the deletion is not safe as written.

- [ ] **Step 2: Delete the action and its tests**

```bash
git rm photolib/actions/clear_stale_trees.py tests/test_action_clear_stale.py
```

- [ ] **Step 3: Delete the now-unreachable repo method**

In `photolib/db/media_repo.py`, delete the whole `uploaded_by_name` method — its `def` line, docstring and body, including the `with self._lock:` block added by the locking work:

```python
    def uploaded_by_name(self) -> dict[str, sqlite3.Row]:
        """Verified uploads keyed by original filename.

        Only rows Drive confirmed: `done`, with a file id and an MD5. This is
        the evidence Clear Stale Trees gates on.
        """
        with self._lock:
            rows = self._conn.execute(
                f"{_UPLOAD_SELECT} WHERE m.upload_status = 'done' "
                "AND m.drive_file_id IS NOT NULL AND m.md5 IS NOT NULL"
            )
            return {row["name"]: row for row in rows}
```

Leave `verified_by_crc` exactly as it is.

- [ ] **Step 4: Delete its test**

In `tests/test_media_repo_uploads.py`, delete `test_uploaded_by_name_only_counts_verified_uploads` (around line 95) in full, including its decorator if it has one.

- [ ] **Step 5: Update the README**

In `README.md`, delete this row from the actions table (around line 113):

```
| Clear Stale Trees | Moves a redundant extracted tree to Drive's trash | Yes |
```

And delete the whole `## Clearing the stale trees` section (around lines 193-200), from the heading through to the blank line before `## Browsing and tagging`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: **620 passed** (628 − 7 clear-stale tests − 1 `uploaded_by_name` test).

If a test fails with `AttributeError: 'MediaRepo' object has no attribute 'uploaded_by_name'`, Step 1's grep missed a caller. Report which one rather than restoring the method.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete Clear Stale Trees and its name-only matcher

A one-time migration cleanup the operator has completed. Its eligibility
check keyed on filename alone, where the rest of the project uses
(crc32, size), so a same-named different photo could be trashed.

clear_stale_trees.py:89 was the only production caller of
MediaRepo.uploaded_by_name, which dies with it. That also settles the
'verified_by_crc duplicates uploaded_by_name' note left open at merge."
```

---

### Task 2: Delete Clear Duplicates, keeping its unique coverage

**Files:**
- Delete: `photolib/actions/clear_duplicates.py`
- Delete: `tests/test_action_clear_dupes.py`, `tests/test_action_clear_dupes_summary.py`
- Modify: `photolib/dedupe.py` (drop the `stamp` parameter)
- Modify: `tests/test_dedupe.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `dedupe.apply_removal(writer, removal, conn) -> None` — three positional parameters, no `stamp`. Task 3 does not touch it; Plan B's tags phase does not touch it.

**Why this matters.** Clear Duplicates wraps `dedupe.plan_removals`/`apply_removal`, which is exactly what Reorganize Folders' Dedupe phase does. The core logic is already unit-tested in `tests/test_dedupe.py` (keeper selection, zero-byte handling, removal fields, replay safety) and the flow integration is covered by `tests/test_action_reorganize_library.py` (dry run, persisted plan, confirm-executes-plan, resume, failed-item resume, dedupe-before-repack ordering).

One behaviour is unique to the action and must be ported first: `test_a_confirmed_batch_shares_one_trashed_at_stamp` exercises `apply_removal`'s `stamp` parameter. After the action is gone, `reorganize_library.py:240` is the only caller and it does not pass a stamp — so the parameter is dead. This task removes it rather than leaving an untested, uncalled argument behind.

- [ ] **Step 1: Confirm the coverage overlap before deleting anything**

Run:
```bash
grep -n "^def test" tests/test_dedupe.py
grep -n "^def test" tests/test_action_clear_dupes.py tests/test_action_clear_dupes_summary.py
grep -rn "apply_removal(" --include="*.py" photolib
```

Expected: `tests/test_dedupe.py` contains `test_a_verified_upload_is_preferred_as_the_keeper` and `test_zero_byte_files_are_reported_not_removed`, which are the unit-level equivalents of the action tests of the same names. `apply_removal` has exactly two callers: `photolib/actions/clear_duplicates.py:109` (passes `stamp`) and `photolib/actions/reorganize_library.py:240` (does not).

If `test_dedupe.py` lacks either of those two tests, **stop and report** — the action tests are not redundant and must be ported, not deleted.

- [ ] **Step 2: Write the failing test for the one unique behaviour**

The stamp test currently lives at the action level. Move it down to the unit level. Add to `tests/test_dedupe.py`, which already imports `apply_removal` and `plan_removals` directly and builds its library with the `_library()` helper:

```python
def test_apply_removal_stamps_the_catalog_with_the_trash_time(conn):
    """Trashing must record `trashed_at`, so the Library stops showing the
    copy immediately rather than waiting for the next Scan."""
    removals, _zero, _total = plan_removals(_library(), conn, "root")
    assert removals, "fixture must produce at least one removal"

    apply_removal(_IdempotentTrashWriter(), removals[0], conn)

    row = conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = ?",
        (removals[0].drive_id,),
    ).fetchone()
    assert row["trashed_at"] is not None
```

`_library()` and `_IdempotentTrashWriter` are defined at the top of that file; `plan_removals` and `apply_removal` are imported at line 3. Do not add new imports or fixtures.

Note `plan_removals` needs the `drive_files` rows to exist for the `UPDATE` to match. If the assertion fails because no row was updated, check how `test_apply_removal_is_safe_to_replay` seeds `drive_files` and mirror it.

- [ ] **Step 3: Run it to verify it passes against current code**

Run: `uv run pytest tests/test_dedupe.py -q`
Expected: PASS. This test describes behaviour that already works — it is being *moved*, not introduced, so green here is correct. Its purpose is to hold the behaviour after `test_action_clear_dupes_summary.py` is deleted.

- [ ] **Step 4: Delete the action and its tests**

```bash
git rm photolib/actions/clear_duplicates.py \
       tests/test_action_clear_dupes.py \
       tests/test_action_clear_dupes_summary.py
```

- [ ] **Step 5: Drop the now-dead `stamp` parameter**

In `photolib/dedupe.py`, drop the parameter from the signature and the paragraph documenting it, keeping the replay-safety paragraph exactly as it is:

```python
def apply_removal(writer, removal: Removal, conn) -> None:
    """Trash the redundant copy and stamp it trashed in the catalog.

    Safe to replay: Drive's trash guide says a trashed file stays retrievable
    by `files.get` — and therefore still patchable — until it is
    auto-deleted 30 days later
    (https://developers.google.com/workspace/drive/api/guides/delete), so
    setting `trashed: true` on a file that is already trashed is just
    resetting a field to the value it already has, not an error. The
    `UPDATE` below is an ordinary overwrite, harmless to repeat.
    """
```

Then collapse the two-line default in the body. It currently reads:

```python
    writer.trash(removal.drive_id)
    if stamp is None:
        stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
```

which becomes:

```python
    writer.trash(removal.drive_id)
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
```

The `datetime`/`timezone` imports stay — they are now the only path rather than the fallback.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_dedupe.py tests/test_action_reorganize_library.py -q`
Expected: PASS.

If `test_action_reorganize_library.py` fails on the signature, `reorganize_library.py:240` was passing a fourth argument after all — check it and report.

- [ ] **Step 7: Update the README**

Delete this row from the actions table:

```
| Clear Duplicates | Trashes byte-identical copies inside the Global Photos folder, keeping one | Yes |
```

- [ ] **Step 8: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: **610 passed** (620 − 8 − 3 deleted + 1 ported).

```bash
git add -A
git commit -m "refactor: delete Clear Duplicates, a wrapper over the flow's Dedupe phase

Its core logic is unit-tested in test_dedupe.py and its flow integration
in test_action_reorganize_library.py. The one behaviour unique to the
action — apply_removal's shared trashed_at stamp — moves down to
test_dedupe.py first.

With clear_duplicates gone, reorganize_library is apply_removal's only
caller and never passes a stamp, so the parameter goes too."
```

---

### Task 3: Delete Repack Buckets, keeping its unique coverage

**Files:**
- Delete: `photolib/actions/reorganize.py`
- Delete: `tests/test_action_reorganize.py`
- Modify: `tests/test_action_reorganize_stale_parent.py` (re-point at the flow)
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: nothing.

**Why this matters.** Repack Buckets is not merely redundant — it is **wrong** in a way the flow is not. It calls `repack.targets_for(ctx.conn)` with no exclusions, while `reorganize_library.py:205` calls `repack.plan_moves(ctx.drive, ctx.conn, root.id, exclude=doomed)`. That `exclude` is load-bearing; `reorganize_library.py:9-10` says so:

> *"Dedupe runs before Repack deliberately: a file about to be trashed must not reserve space in a bucket, or every dedupe would leave the layout wrong."*

Running the standalone action reserves bucket space for files that are about to be trashed, producing exactly the layout the flow exists to avoid. Keeping it as a hidden-but-reachable page would leave a live footgun.

`tests/test_repack.py` already unit-tests the core: moves, `test_excluded_files_do_not_reserve_bucket_space`, name collisions, sweep, and replay safety for both `apply_move` and `apply_sweep`. The flow's integration is covered by `tests/test_action_reorganize_library.py`.

`tests/test_action_reorganize_stale_parent.py` is different: its two tests describe real Drive behaviour — a move whose `removeParents` is already stale — not an artifact of the wrapper. They must survive, re-pointed at the flow.

- [ ] **Step 1: Read the two stale-parent tests before touching them**

Run: `cat tests/test_action_reorganize_stale_parent.py`

Note what each test asserts and which fixture it builds. You are about to re-point them at `reorganize_library`, which is confirm-gated and phase-based, so the invocation changes even though the assertions should not.

- [ ] **Step 2: Confirm the rest of the coverage is genuinely duplicated**

Run:
```bash
grep -n "^def test" tests/test_repack.py
grep -n "^def test" tests/test_action_reorganize.py
```

Expected: `tests/test_repack.py` covers `test_a_file_already_in_its_bucket_produces_no_move`, `test_a_file_in_the_wrong_folder_is_moved`, `test_excluded_files_do_not_reserve_bucket_space`, `test_a_name_collision_in_the_destination_is_renamed`, `test_sweep_lists_only_empty_folders`, `test_apply_sweep_is_safe_to_replay`, `test_apply_move_is_safe_to_replay_when_drive_rejects_a_stale_removeparents`, `test_apply_move_reraises_a_genuine_drive_failure`.

For each test in `test_action_reorganize.py`, find its counterpart in `test_repack.py` or `test_action_reorganize_library.py`. **List any with no counterpart and port those before deleting the file.** Do not skip this — it is the only thing standing between this task and lost coverage.

- [ ] **Step 3: Re-point the stale-parent tests at the flow**

Rename the file so it sits with the flow's other tests:

```bash
git mv tests/test_action_reorganize_stale_parent.py tests/test_reorganize_library_stale_parent.py
```

In the renamed file, replace the import:

```python
from photolib.actions import reorganize
```

with:

```python
from photolib.actions import reorganize_library
```

and change each invocation from the action's single call to the flow's two-pass confirm cycle. Where a test does:

```python
    list(reorganize.run(ctx, reorganize.Params(confirm=True)))
```

it becomes:

```python
    list(reorganize_library.run(ctx, reorganize_library.Params()))
    list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))
```

The first pass writes the plan into `job_items`; the second executes it. A confirm with no preceding dry run is refused (`reorganize_library.py:87`), so both passes are required. The context fixture must supply `ctx.run_id` — reuse `reorg_context` from `tests/test_action_reorganize_library.py` if the existing fixture does not, rather than building a new one.

- [ ] **Step 4: Run the re-pointed tests**

Run: `uv run pytest tests/test_reorganize_library_stale_parent.py -v`
Expected: PASS, 2 tests.

If a test now fails because the flow trashes the file during Dedupe before Repack sees it, adjust the **fixture** so the file is not a duplicate — do not weaken the assertion. Report what you changed.

- [ ] **Step 5: Delete the action and its tests**

```bash
git rm photolib/actions/reorganize.py tests/test_action_reorganize.py
```

- [ ] **Step 6: Update the README**

Delete this row from the actions table:

```
| Repack Buckets | Moves every indexed file into its bucket folder, trashing what's left empty | Yes |
```

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q`
Expected: **596 passed** (610 − 14), assuming Step 2 found nothing to port. If you ported tests, the count is 596 plus however many you added — say so in your report.

```bash
git add -A
git commit -m "refactor: delete Repack Buckets, which diverged from the flow

It called repack.targets_for with no exclusions while the flow calls
plan_moves(exclude=doomed), so it reserved bucket space for files Dedupe
was about to trash — the exact layout the ordering exists to prevent.

Core logic stays covered by test_repack.py. The stale-parent tests
describe real Drive behaviour, not the wrapper, so they move to
test_reorganize_library_stale_parent.py against the flow."
```

---

### Task 4: Re-point the job-test fixtures off `check_connection`

**Files:**
- Modify: `tests/test_jobs_runner.py`
- Modify: `tests/test_api_jobs.py`
- Modify: `tests/test_actions.py`
- Modify: `web/src/pages/ActionPage.test.tsx`

**Interfaces:**
- Consumes: nothing from Tasks 1-3.
- Produces: no test in the suite depends on `check_connection` being a **registered action**. Task 5 relies on this completely — do not start Task 5 until this task is green.

**Why this matters.** `check_connection` is used across the suite as the generic "cheap action to run": `tests/test_jobs_runner.py` calls `runner.submit("check_connection", {})` about ten times and `registry.get_action("check_connection")` three times; `tests/test_api_jobs.py` POSTs to `/api/actions/check_connection/run` seven times. The runner resolves the id through `registry.get_action`, so the moment Task 5 moves the module out of the registry, every one of those raises `UnknownActionError` or returns 404.

Doing this as its own task means Task 5's diff is a pure move, and a failure there cannot be confused with fixture churn.

`verify_library` is the right substitute: it is the only non-flow action that survives, its `Params` has no fields (so `test_an_empty_run_id_is_rejected_rather_than_treated_as_absent` still exercises `extra="forbid"`), and with no `photos_root` configured it yields a single error-level event and returns. That still marks the job `done` — `runner.py:243` calls `mark_failed` only on an exception, not on an error-level event — so job-lifecycle assertions hold.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_actions.py`, replacing `test_registry_discovers_check_connection` (line 22) and `test_registry_specs_are_complete` (line 27) entirely:

```python
def test_the_registry_holds_only_the_flows_and_the_tools():
    """The registry publishes a page per entry, so anything discovered here
    is a page a user can reach. Implementation phases must not appear."""
    ids = {spec.id for spec in all_actions()}
    assert ids == {"sync_archives", "reorganize_library", "sync_tags",
                   "verify_library"}


def test_registry_specs_are_complete():
    spec = get_action("verify_library")
    assert spec.title
    assert spec.description
    assert spec.json_schema()["type"] == "object"
```

The set includes `sync_tags` because Plan B has not run yet. Plan B's first task removes it from this assertion.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_actions.py::test_the_registry_holds_only_the_flows_and_the_tools -v`

Expected: FAIL — the actual set still contains `check_connection`, `organize`, `pair_metadata`, `plan_organize` and `scan_archives`. This test stays red until Task 5 and is the tripwire proving the demotion took effect.

- [ ] **Step 3: Re-point the sorting anchor**

`test_well_formed_second_module_is_discovered` compares its temp module against `check_connection` as a sort anchor. In `tests/test_actions.py`, replace:

```python
    # Verify sorting by (order, id)
    check_conn_spec = get_action("check_connection")
    assert (check_conn_spec.order, check_conn_spec.id) < (spec.order, spec.id)
```

with:

```python
    # Verify sorting by (order, id): verify_library is ORDER 90, the temp
    # module is ORDER 100, and neither is a flow.
    anchor = get_action("verify_library")
    assert (anchor.order, anchor.id) < (spec.order, spec.id)
```

- [ ] **Step 4: Fix the `check_connection` import and its behaviour tests**

`tests/test_actions.py:7` imports the module directly:

```python
from photolib.actions import check_connection
```

Leave this import alone for now — Task 5 changes it. The four tests using it (`test_check_connection_reports_missing_settings`, `test_check_connection_reports_configured_folders`, `test_check_connection_reports_unreachable_folder`, `test_progress_is_monotonic_and_bounded`) call `check_connection.run` directly rather than through the registry, so they keep working either way.

- [ ] **Step 5: Re-point the runner tests**

In `tests/test_jobs_runner.py`, replace every `"check_connection"` with `"verify_library"`:

```bash
sed -i '' 's/"check_connection"/"verify_library"/g' tests/test_jobs_runner.py
```

Then read the file and check the three `registry.get_action(...)` sites (around lines 52, 133, 154) — they monkeypatch a spec, and the surrounding comments may name `check_connection` in prose. Update any comment that now reads false.

- [ ] **Step 6: Re-point the API job tests**

In `tests/test_api_jobs.py`, replace every occurrence:

```bash
sed -i '' 's|/api/actions/check_connection/run|/api/actions/verify_library/run|g' tests/test_api_jobs.py
sed -i '' 's/"check_connection"/"verify_library"/g' tests/test_api_jobs.py
```

Then fix the docstring at line 88, which reads *"check_connection has no run_id param; extra='forbid' would reject it."* — change the name to `verify_library`. The claim stays true: its `Params` is also fieldless.

- [ ] **Step 7: Re-point the API action-list test**

In `tests/test_api_actions.py`, `test_lists_actions_with_schema` (line 41) asserts on `check_connection` and its title. Change it to:

```python
def test_lists_actions_with_schema(client):
    actions = client.get("/api/actions").json()
    assert any(a["id"] == "verify_library" for a in actions)
    spec = next(a for a in actions if a["id"] == "verify_library")
    assert spec["title"] == "Verify Library"
    assert spec["description"]
    assert spec["schema"]["type"] == "object"
```

And in `test_run_creates_a_job` (line 50), change the posted id and the `job["action"]` assertion from `check_connection` to `verify_library`.

- [ ] **Step 8: Re-point the frontend mocks**

In `web/src/pages/ActionPage.test.tsx`, the mocks at lines 50, 58 and 66 use `group: 'advanced'`, and the test at line 173 renders `/actions/check_connection` with a comment explaining it is an advanced action. These are pure mocks — no registry is involved — but they should not describe a world that no longer exists. Change `group: 'advanced'` to `group: 'tool'` at all three sites, change `renderAt('/actions/check_connection')` to `renderAt('/actions/verify_library')`, and update the comment:

```tsx
  it('does not offer to confirm a run that is not a finished flow', async () => {
    // Tool action: getJob's default mock ('verify_library', 'done') applies,
    // but group is 'tool', not 'flow'.
    renderAt('/actions/verify_library')
```

Check the default `getJob` mock near the top of the file — if it hardcodes `'check_connection'` as the action name, change it to `'verify_library'` so the comment stays true.

- [ ] **Step 9: Run everything**

Run: `uv run pytest -q`
Expected: **596 passed, 1 failed** — only `test_the_registry_holds_only_the_flows_and_the_tools`, which stays red until Task 5.

Run: `cd web && npm test && npx tsc -b && cd ..`
Expected: 156 passed, no type errors.

- [ ] **Step 10: Commit**

The red test is committed deliberately — it is the spec for Task 5.

```bash
git add -A
git commit -m "test: stop using check_connection as the generic job fixture

The runner resolves ids through registry.get_action, so every
submit('check_connection') and POST /api/actions/check_connection/run
breaks the moment the module leaves the registry. Re-points them at
verify_library, which survives as the one tool: fieldless Params, and a
missing photos_root yields an error event rather than raising, so the job
still reaches 'done'.

Adds test_the_registry_holds_only_the_flows_and_the_tools, deliberately
red until the steps/ move lands."
```

---

### Task 5: Move the five phase modules into `steps/`

**Files:**
- Create: `photolib/actions/steps/__init__.py`
- Move: `photolib/actions/{check_connection,scan_archives,pair_metadata,plan_organize,organize}.py` → `photolib/actions/steps/`
- Modify: `photolib/actions/sync_archives.py:13-19`
- Modify: `tests/test_actions.py`, `tests/test_action_sync_archives.py`, `tests/test_action_plan.py`, `tests/test_action_pair.py`, `tests/test_action_scan.py`, `tests/test_action_organize.py`, `tests/test_live_phase2.py`, `tests/test_live_phase3.py`

**Interfaces:**
- Consumes: Task 4's guarantee that no test depends on `check_connection` being registered.
- Produces: `photolib.actions.steps.{check_connection,scan_archives,pair_metadata,plan_organize,organize}`, each keeping its existing `Params` and `run` unchanged. `registry.all_actions()` returns exactly `sync_archives`, `reorganize_library`, `sync_tags`, `verify_library`.

**Why this matters.** These five are not optional extras — `sync_archives.py:13-19` imports them and drives each one's `module.run` / `module.Params()` through `run_phase`. They must keep working; they must stop being pages.

`registry._discover` iterates `pkgutil.iter_modules(photolib.actions.__path__)`, which is **non-recursive**. It will yield `steps` itself, `importlib.import_module` will load the empty `__init__.py`, that has none of the six required attributes, and the `all(hasattr(...))` check will skip it. The submodules are never visited. So the subpackage de-registers them with no registry change at all, and the directory name states the intent in a way that four deleted constants would not.

- [ ] **Step 1: Create the package**

```bash
mkdir -p photolib/actions/steps
```

Create `photolib/actions/steps/__init__.py`:

```python
"""Phases of a flow, not actions in their own right.

`registry._discover` walks `photolib/actions/` with `pkgutil.iter_modules`,
which does not recurse. Living in this subpackage is therefore what keeps
these modules out of the registry — and so out of the sidebar — while
`sync_archives` goes on importing and running them directly.

Nothing here declares ID/TITLE/DESCRIPTION/ORDER. A module that wants to be
an action belongs one directory up.
"""
```

- [ ] **Step 2: Move the five modules**

```bash
git mv photolib/actions/check_connection.py photolib/actions/steps/
git mv photolib/actions/scan_archives.py photolib/actions/steps/
git mv photolib/actions/pair_metadata.py photolib/actions/steps/
git mv photolib/actions/plan_organize.py photolib/actions/steps/
git mv photolib/actions/organize.py photolib/actions/steps/
```

- [ ] **Step 3: Point the flow at the new location**

In `photolib/actions/sync_archives.py`, replace lines 13-19:

```python
from photolib.actions import (
    check_connection,
    organize,
    pair_metadata,
    plan_organize,
    scan_archives,
)
```

with:

```python
from photolib.actions.steps import (
    check_connection,
    organize,
    pair_metadata,
    plan_organize,
    scan_archives,
)
```

- [ ] **Step 4: Run the suite to find every remaining import**

Run: `uv run pytest -q 2>&1 | tail -30`

Expected: a wall of `ModuleNotFoundError: No module named 'photolib.actions.scan_archives'` and similar. This is the point of running it now — the errors enumerate exactly what Step 5 must fix.

- [ ] **Step 5: Update the test imports**

Rewrite these imports mechanically. The module contents did not change, only their path.

```bash
sed -i '' \
  -e 's/^from photolib\.actions import check_connection$/from photolib.actions.steps import check_connection/' \
  -e 's/^from photolib\.actions\.\(check_connection\|scan_archives\|pair_metadata\|plan_organize\|organize\) import /from photolib.actions.steps.\1 import /' \
  tests/*.py
```

Three import lines are compound and the `sed` above will not catch them. Fix each by hand:

`tests/test_action_sync_archives.py:5`

```python
from photolib.actions import check_connection, scan_archives, sync_archives
```

becomes:

```python
from photolib.actions import sync_archives
from photolib.actions.steps import check_connection, scan_archives
```

`tests/test_action_organize.py:8`

```python
from photolib.actions import organize, verify_library
```

becomes:

```python
from photolib.actions import verify_library
from photolib.actions.steps import organize
```

`tests/test_action_plan.py:11` imports `run` from `plan_organize` and `tests/test_action_plan.py:332` re-imports `verdict_for` inside a test body — both need the `.steps` prefix.

- [ ] **Step 6: Update the monkeypatch target strings**

`monkeypatch.setattr` takes a dotted **string**, which `sed` on import lines will not have touched. In `tests/test_action_organize.py`, lines 167 and 274:

```python
    monkeypatch.setattr("photolib.actions.organize.transfer_entry", explode)
```

becomes:

```python
    monkeypatch.setattr(
        "photolib.actions.steps.organize.transfer_entry", explode
    )
```

Find every remaining one:

```bash
grep -rn "photolib\.actions\.\(check_connection\|scan_archives\|pair_metadata\|plan_organize\|organize\)" tests/ --include="*.py"
```

Expected after fixing: no hits outside `photolib.actions.steps.*`, except `tests/test_action_reorganize_library.py:323`, which patches `photolib.actions.reorganize_library.scan.index_destination` — a different module that does not move. Leave that one alone.

A stale dotted path in `monkeypatch.setattr` raises `ModuleNotFoundError` rather than silently patching nothing, so Step 8's run catches any you miss. Fixing them here just makes the failure list shorter.

- [ ] **Step 7: Add the test that pins the mechanism**

Add to `tests/test_actions.py`:

```python
def test_modules_under_steps_are_not_discovered_as_actions():
    """The subpackage is the mechanism, not a naming convention.

    pkgutil.iter_modules does not recurse, so a module in steps/ cannot
    become a page no matter what attributes it declares. If someone
    flattens the package back out, this fails.
    """
    ids = {spec.id for spec in all_actions()}
    for phase in (
        "check_connection", "scan_archives", "pair_metadata",
        "plan_organize", "organize",
    ):
        assert phase not in ids

    # ...and they are still importable and still runnable by the flow.
    from photolib.actions.steps import organize

    assert callable(organize.run)
    assert organize.Params().model_dump() is not None
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest -q`
Expected: **598 passed** (596 + the 2 new registry tests). The tripwire from Task 4, `test_the_registry_holds_only_the_flows_and_the_tools`, now passes.

If it still fails, print the actual set and compare — a stray module left in `photolib/actions/` is the likely cause.

- [ ] **Step 9: Verify the flow still runs end to end**

Run: `uv run pytest tests/test_action_sync_archives.py tests/test_jobs_runner.py tests/test_api_jobs.py -v`
Expected: PASS. This is the real check that the move did not break Sync from Archives.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: move Sync from Archives' phases into actions/steps/

check_connection, scan_archives, pair_metadata, plan_organize and organize
are the flow's implementation — sync_archives drives each one's run() —
but the registry published each as a page purely because it was importable.

pkgutil.iter_modules does not recurse, so a subpackage de-registers them
with no registry change, and the directory name states the intent better
than four deleted constants would."
```

---

### Task 6: Regroup Verify Library as a tool and rebuild the sidebar

**Files:**
- Modify: `photolib/actions/verify_library.py`
- Modify: `web/src/components/Nav.tsx`
- Modify: `web/src/components/Nav.test.tsx`
- Modify: `web/src/api/types.ts:35`

**Interfaces:**
- Consumes: Task 5's registry contents.
- Produces: `verify_library` carries `GROUP = "tool"`. `ActionSpec.group` is `'flow' | 'tool'` on the frontend. No action carries `"advanced"` any more.

**Why this matters.** After Task 5 the registry holds four entries, of which `verify_library` is the only one still relying on `ActionSpec.group`'s `"advanced"` default (`base.py:62`). The sidebar's `Advanced` disclosure is now a collapsed section holding one diagnostic, which misdescribes it.

`sync_tags` also still defaults to `"advanced"` and stays visible until Plan B folds it. Nav must therefore render both `tool` and any residual `advanced` entries, or Sync Tags disappears from the UI while still being the only way to push tags to Drive. This task keeps the filter permissive; Plan B tightens it.

- [ ] **Step 1: Write the failing test**

Replace `test_separates_flows_from_advanced_actions` in `web/src/components/Nav.test.tsx` with:

```tsx
  it('puts flows and tools in their own sections', () => {
    const actions = [
      { id: 'sync_archives', title: 'Sync from Archives', description: '', order: 1, group: 'flow', schema: { type: 'object' } },
      { id: 'verify_library', title: 'Verify Library', description: '', order: 90, group: 'tool', schema: { type: 'object' } },
    ] as ActionSpec[]
    render(<MemoryRouter><Nav actions={actions} /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Flows' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Tools' })).toBeInTheDocument()
    expect(screen.queryByText('Advanced')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sync from Archives' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Verify Library' })).toBeInTheDocument()
  })
```

Also update the two tests above it: the module-level `ACTIONS` constant uses `group: 'advanced'` for `scan_archives`, which no longer exists as an action. Change it to:

```tsx
const ACTIONS = [
  { id: 'verify_library', title: 'Verify Library', description: '', order: 90, group: 'tool', schema: { type: 'object' } },
] as ActionSpec[]
```

and change `test_still_lists_the_actions_it_is_given`'s assertion from `{ name: 'Scan Archives' }` to `{ name: 'Verify Library' }`.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- Nav && cd ..`
Expected: FAIL — no `Tools` heading; the component still renders the `Advanced` disclosure.

- [ ] **Step 3: Declare the group on the action**

In `photolib/actions/verify_library.py`, after `ORDER = 90`:

```python
ORDER = 90
GROUP = "tool"
```

- [ ] **Step 4: Rebuild the sidebar section**

In `web/src/components/Nav.tsx`, replace the `advanced` binding and the `<details>` block:

```tsx
  const flows = actions.filter((a) => a.group === 'flow')
  const tools = actions.filter((a) => a.group !== 'flow')
```

and:

```tsx
      <section>
        <h2>Tools</h2>
        {tools.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
```

`!== 'flow'` rather than `=== 'tool'` on purpose: `sync_tags` still defaults to `"advanced"` until Plan B folds it, and an exact match would drop it out of the sidebar entirely while it is still the only way to push tags to Drive.

- [ ] **Step 5: Widen the frontend type**

In `web/src/api/types.ts:35`:

```ts
  group: 'flow' | 'advanced'
```

becomes:

```ts
  group: 'flow' | 'tool' | 'advanced'
```

`'advanced'` stays in the union only because `sync_tags` still emits it; Plan B removes it.

- [ ] **Step 6: Run the frontend tests**

Run: `cd web && npm test && npx tsc -b && cd ..`
Expected: 156 passed, no type errors.

- [ ] **Step 7: Run the backend suite**

Run: `uv run pytest tests/test_api_actions.py tests/test_actions.py -q`
Expected: PASS. `test_action_list_exposes_the_group` asserts only that the key is present, so it is unaffected.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: Advanced becomes Tools, holding the one diagnostic

verify_library was the last action relying on ActionSpec.group's
'advanced' default. It declares GROUP = 'tool' and the sidebar renders a
Tools section instead of a collapsed Advanced disclosure.

The filter stays !== 'flow' rather than === 'tool' because sync_tags is
still an action until Plan B folds it, and an exact match would drop it
from the sidebar while it remains the only way to push tags to Drive."
```

---

### Task 7: Reconcile the docs and verify the whole refactor

**Files:**
- Modify: `README.md`
- Test: whole suite, both sides

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

**Why this matters.** Tasks 1-3 each removed one README row as they went. This task checks the result reads as a coherent whole rather than a table with three holes in it, and runs the full verification the plan's baseline promised.

- [ ] **Step 1: Bring the actions table in line with reality**

The table lives under a `## Advanced` heading at line 99, introduced by:

> *"The phases above also exist as their own actions, for re-running a single phase in isolation or for recovering a flow that needs a nudge partway through."*

That sentence is the thesis this whole refactor rejects — resumable jobs replaced it. Rename the heading to `## Actions` and replace that introduction with:

```markdown
Two flows do the work. Two further actions stand alone: one pushes tags to
Drive, one reports drift. Everything else is a phase inside a flow, not a
button — a killed run resumes from `job_items` rather than needing a phase
re-run by hand.
```

Then replace the whole table with:

```markdown
| Action | Does | Writes to Drive |
| --- | --- | --- |
| Sync from Archives | Extracts every file from the ZIPs into the Global Photos folder, skipping what is already there | Yes, after confirm |
| Reorganize Folders | Indexes, enriches, dedupes, repacks into ~100-file buckets and sweeps the empties | Yes, after confirm |
| Sync Tags to Drive | Mirrors tags onto each file's `appProperties` | Yes |
| Verify Library | Compares the catalog against what Drive actually holds and reports drift | No |
```

- [ ] **Step 2: Correct the "Adding an action" contract**

`## Adding an action` (line 274) documents exactly the contract this refactor changes, including the sentence *"An optional `GROUP` attribute, `"flow"` or `"advanced"`…"*. Replace that section's body with:

```markdown
Create a module in `photolib/actions/` declaring `ID`, `TITLE`, `DESCRIPTION`,
`ORDER`, a `Params` model extending `ActionParams`, and a `run(ctx, params)`
generator that yields `ProgressEvent`s. The registry discovers it
automatically and the frontend renders a page for it — no frontend changes
required. An optional `GROUP` attribute, `"flow"` or `"tool"`, decides which
section of the sidebar it lands in. `ORDER` sorts within a group, not across
both.

Discovery does **not** recurse. A module under `photolib/actions/steps/` is
therefore a phase of a flow, never a page, however many of those attributes
it declares — that is what keeps Sync from Archives' five phases out of the
sidebar. Put a new phase there and import it from its flow; put a new
standalone action one directory up.
```

- [ ] **Step 3: Describe the phases where they now live**

Immediately after the table, add:

```markdown
Sync from Archives runs five phases — Connect, Scan, Pair, Plan, Upload —
implemented in `photolib/actions/steps/`. They are deliberately not actions:
`registry._discover` walks `photolib/actions/` without recursing, so a module
in `steps/` cannot become a page. Run the flow, not the phase; a killed run
resumes from `job_items` rather than restarting.
```

- [ ] **Step 4: Check nothing else in the README describes a deleted action**

Run:
```bash
grep -n -i "clear stale\|stale tree\|repack buckets\|clear duplicates\|advanced" README.md
```

Expected: no hits. Fix any that remain — a stale README is the most likely thing to survive this refactor.

- [ ] **Step 5: Confirm the registry is what the spec promised**

Run:
```bash
uv run python -c "
from photolib.actions.registry import all_actions
for s in sorted(all_actions(), key=lambda s: (s.group != 'flow', s.order)):
    print(f'{s.group:8} {s.order:3} {s.id}')
"
```

Expected exactly four lines:

```
flow       1 sync_archives
flow       2 reorganize_library
advanced  60 sync_tags
tool      90 verify_library
```

`sync_tags` is still present and still `advanced` — Plan B removes it. Any other id means something was missed.

- [ ] **Step 6: Full verification**

Run: `uv run pytest -q`
Expected: **598 passed, 14 deselected.**

Run: `cd web && npm test && npx tsc -b && cd ..`
Expected: 156 passed, no type errors.

If the backend count differs, reconcile it against the arithmetic — 628 baseline, −8 (Task 1), −10 (Task 2), −14 (Task 3), +2 (Task 5) — and report the discrepancy rather than editing the expectation.

- [ ] **Step 7: Confirm nothing dead is left behind**

Run:
```bash
grep -rn "clear_stale_trees\|clear_duplicates\|uploaded_by_name" --include="*.py" photolib tests
grep -rn "from photolib.actions import reorganize\b" --include="*.py" photolib tests
```

Expected: no hits from either.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "docs: README describes the four reachable actions

The table listed ten entries, six of which are now either deleted or
internal phases. Documents steps/ and why a module there cannot become a
page, so the next person does not helpfully 'fix' the layout."
```

---

## Self-review notes

Checked against `docs/superpowers/specs/2026-08-12-retire-advanced-actions-design.md`:

- Every spec decision has a task: deletions (1, 2, 3), the `steps/` move (5), the regrouping and nav (6), docs (1, 2, 3, 7).
- The spec's `uploaded_by_name` knock-on is Task 1. A second knock-on found while writing this plan — `dedupe.apply_removal`'s `stamp` parameter loses its only caller with `clear_duplicates` — is Task 2, Step 5. It is not in the spec; flag it at review.
- The spec's Sync Tags fold is **Plan B** and appears here only as the reason Task 6 filters `!== 'flow'` and Task 4's registry assertion still lists `sync_tags`.
- Task ordering is load-bearing in one place: Task 4 must precede Task 5, because the runner resolves ids through `registry.get_action` and moving `check_connection` breaks ~20 fixture sites. Tasks 1-3 are independent of each other and of 4-5.

## Not in scope

- Anything in Plan B: the `tags` phase, deleting `sync_tags.py`, reusing the Index walk's appProperties, retiring `sync_tags.PREFIX` in favour of `enrich.TAG_PREFIX`.
- Removing `ActionSpec.group`'s `"advanced"` default (`base.py:62`) and narrowing the frontend union to `'flow' | 'tool'` — both wait for Plan B, when `sync_tags` stops being the last action that needs them.
- Folding `verify_library` into a flow. The operator kept it standalone deliberately.
- `tests/test_jobs_repo.py`'s `repo.create("check_connection", {})` calls. `JobsRepo` stores the action id as an opaque string and never consults the registry, so these keep passing. They are cosmetically stale, not broken.

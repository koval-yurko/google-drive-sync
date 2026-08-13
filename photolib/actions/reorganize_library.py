"""Make the Global Photos folder tidy, whatever route its files took.

Five phases behind one confirm gate. The dry run writes every intended
operation into `job_items`; confirming reads those rows back and executes
exactly what was reported, marking each done as it goes. A killed confirm run
therefore resumes rather than re-planning, and the plan you approved is the
plan that runs.

Dedupe runs before Repack deliberately: a file about to be trashed must not
reserve space in a bucket, or every dedupe would leave the layout wrong.
"""

from __future__ import annotations

from typing import Iterator

from photolib import dedupe, enrich, places, repack, scan
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.actions.phases import phase_label
from photolib.db.geocache_repo import GeocacheRepo
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.db.tags_repo import DuplicateTagError, TagsRepo
from photolib.drive.errors import DriveError

ID = "reorganize_library"
TITLE = "Reorganize Folders"
DESCRIPTION = (
    "Index the Global Photos folder, fill in dates, countries and tags from "
    "Drive, remove duplicate copies, repack everything into ~100-file bucket "
    "folders and trash the folders left empty. Reports what it would do "
    "unless you confirm."
)
ORDER = 2
GROUP = "flow"

PHASES = ("index", "enrich", "dedupe", "repack", "sweep")
_LABELS = {
    "index": ("Index", (0.00, 0.20), 1),
    "enrich": ("Enrich", (0.20, 0.45), 2),
    "dedupe": ("Dedupe", (0.45, 0.60), 3),
    "repack": ("Repack", (0.60, 0.90), 4),
    "sweep": ("Sweep", (0.90, 1.00), 5),
}


class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None


def _label(phase: str) -> str:
    name, _, index = _LABELS[phase]
    return phase_label(name, index, len(PHASES))


def _progress(phase: str, done: int, total: int) -> float:
    _, (low, high), _ = _LABELS[phase]
    return low + (high - low) * (done / total if total else 1.0)


def _cancelled(ctx: ActionContext) -> bool:
    return ctx.cancelled is not None and ctx.cancelled.is_set()


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    items = JobItemsRepo(ctx.conn)
    scans = ScanRepo(ctx.conn)
    tags = TagsRepo(ctx.conn)
    run_id = ctx.run_id or "adhoc"

    root = ctx.settings.get_folder(PHOTOS_ROOT)
    if root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0, level="error",
        )
        return

    # A completed dry run always writes the "index" item (it is the one
    # phase with no way to plan zero work), so its presence is the honest
    # marker for "a plan exists for this run" — unlike checking dedupe and
    # repack specifically, which are both legitimately empty for a library
    # that is already tidy except for folders left to sweep.
    if params.confirm and not items.all(run_id, "index"):
        yield ProgressEvent(
            "There is no plan for this run to confirm. Run Reorganize Folders "
            "without confirm first, read what it reports, then confirm that "
            "run.",
            progress=1.0, level="error",
        )
        return

    # ---- Index -------------------------------------------------------
    # One item, all-or-nothing: upsert_drive_files sweeps rows that do not
    # carry its timestamp, so a half-finished index would delete what it had
    # not yet reached. Re-walking is cheap and idempotent.
    try:
        walked = scan.index_destination(ctx.drive, ctx.conn, root.id)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot read the Global Photos folder: {exc}",
            progress=1.0, level="error",
        )
        return
    count = len(walked)
    items.put(run_id, "index", root.id, run_id, "done", {"files": count})
    yield ProgressEvent(
        f"Indexed {count} file(s) under {root.name}.",
        progress=_progress("index", 1, 1), phase=_label("index"),
        done=count, total=count,
    )
    if _cancelled(ctx):
        return

    # ---- Enrich ------------------------------------------------------
    # `walked` already carries the appProperties and EXIF location Enrich
    # needs, straight from the walk moments ago — reusing it here is the
    # whole point of I8(a): no second get_file per file. The `.get_file`
    # fallback below is only for a row `unenriched()` finds that this walk
    # did not produce (e.g. another writer inserted it between Index
    # committing and this loop reading `drive_files`); it keeps the same
    # per-item isolation the old always-fetch code had for that edge.
    walked_by_id = {file.id: file for file in walked}
    pending = scans.unenriched()
    geocoder = places.Geocoder(
        GeocacheRepo(ctx.conn), places.api_key_from_env(ctx.config.repo_root)
    )
    total = len(pending)
    for index, row in enumerate(pending, start=1):
        if _cancelled(ctx):
            return
        try:
            file = walked_by_id.get(row["drive_id"])
            if file is None:
                file = ctx.drive.get_file(row["drive_id"])
            result = enrich.enrichment_for(file, geocoder)
            scans.set_enrichment(
                row["drive_id"],
                capture_hint=result.capture_hint,
                latitude=result.latitude,
                longitude=result.longitude,
                country=result.country,
                metadata_source=result.metadata_source,
                # I6: record what Enrich just imported from Drive so
                # sync_tags's candidate query still catches this file if the
                # tag is later removed locally — without this, an untagged
                # file with no `synced_tags` on record drops out of both
                # halves of that query and the property never gets removed.
                synced_tags=",".join(result.tag_slugs),
            )
            for slug in result.tag_slugs:
                tags.add_files(tags.ensure(slug)["id"], [row["drive_id"]])
        except (DriveError, DuplicateTagError, ValueError) as exc:
            # One file trashed between Index and Enrich, or one t_* tag
            # whose suffix collides with an existing canonical slug, must
            # not fail the whole flow before Dedupe, Repack and Sweep run —
            # same reasoning as Repack's per-item guard, below.
            # set_enrichment is never called on this path, so scans still
            # reports the row `unenriched` and a later run retries it.
            items.put(run_id, "enrich", row["drive_id"], run_id, "failed",
                      {"error": str(exc)})
            yield ProgressEvent(
                f"{row['drive_id']}: {exc}",
                progress=_progress("enrich", index, total),
                phase=_label("enrich"), level="error",
                done=index, total=total,
            )
            continue
        items.put(run_id, "enrich", row["drive_id"], run_id, "done",
                  {"source": result.metadata_source, "tags": result.tag_slugs})
        if index % 50 == 0 or index == total:
            yield ProgressEvent(
                f"Enriched {index} of {total}.",
                progress=_progress("enrich", index, total),
                phase=_label("enrich"), done=index, total=total,
            )
    if not total:
        yield ProgressEvent(
            "Nothing new to enrich.", progress=_progress("enrich", 1, 1),
            phase=_label("enrich"), done=0, total=0,
        )

    # ---- Dedupe (plan) -----------------------------------------------
    if not items.all(run_id, "dedupe"):
        removals, zero_byte, _scanned = dedupe.plan_removals(
            ctx.drive, ctx.conn, root.id
        )
        for removal in removals:
            items.put(run_id, "dedupe", removal.drive_id, run_id, "pending",
                      removal.__dict__)
        for drive_id in zero_byte:
            items.put(run_id, "dedupe", drive_id, run_id, "skipped",
                      {"why": "zero-byte file, shares an MD5 with nothing"})
        yield ProgressEvent(
            f"{len(removals)} duplicate copy/copies to trash; "
            f"{len(zero_byte)} zero-byte file(s) left alone.",
            progress=_progress("dedupe", 0, 1), phase=_label("dedupe"),
        )

    doomed = {row["item_key"] for row in items.pending(run_id, "dedupe")}

    # ---- Repack (plan) -----------------------------------------------
    if not items.all(run_id, "repack"):
        moves = repack.plan_moves(ctx.drive, ctx.conn, root.id, exclude=doomed)
        for move in moves:
            items.put(run_id, "repack", move.drive_id, run_id, "pending",
                      move.__dict__)
        yield ProgressEvent(
            f"{len(moves)} file(s) to move into their bucket folder.",
            progress=_progress("repack", 0, 1), phase=_label("repack"),
        )

    if not params.confirm:
        yield ProgressEvent(
            "Nothing has changed in Drive. Re-run with confirm to apply this "
            "plan.",
            progress=1.0,
        )
        return

    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    # ---- Dedupe (apply) ----------------------------------------------
    outstanding = items.pending(run_id, "dedupe")
    for index, row in enumerate(outstanding, start=1):
        if _cancelled(ctx):
            return
        # mark(..., "failed", ...) folds {"error": ...} onto the persisted
        # plan rather than replacing it (job_items_repo.mark), so a
        # previously-failed row's detail carries both; strip it back out
        # before rebuilding the dataclass the plan actually describes.
        plan = {k: v for k, v in row["detail"].items() if k != "error"}
        removal = dedupe.Removal(**plan)
        try:
            dedupe.apply_removal(ctx.writer, removal, ctx.conn)
        except DriveError as exc:
            items.mark(run_id, "dedupe", row["item_key"], "failed",
                       {"error": str(exc)})
            yield ProgressEvent(f"{removal.name}: {exc}",
                                progress=_progress("dedupe", index,
                                                   len(outstanding)),
                                phase=_label("dedupe"), level="error",
                                done=index, total=len(outstanding))
            continue
        items.mark(run_id, "dedupe", row["item_key"], "done")
        yield ProgressEvent(
            f"Trashed {removal.parent_path}/{removal.name}",
            progress=_progress("dedupe", index, len(outstanding)),
            phase=_label("dedupe"), done=index, total=len(outstanding),
        )

    # ---- Repack (apply) ----------------------------------------------
    outstanding = items.pending(run_id, "repack")
    if _cancelled(ctx):
        return
    # Every folder that already exists under the root, plus whichever bucket
    # folders this plan's moves need and do not find. `ensure_folders` alone
    # only knows the destinations; a move's *source* folder is just as often
    # one nobody is arriving at this run, and apply_move needs both ids.
    try:
        folder_ids = repack.folder_paths(ctx.drive, root.id)
        folder_ids.update(repack.ensure_folders(
            ctx.writer, root.id,
            sorted({row["detail"]["to_folder"] for row in outstanding}),
        ))
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot prepare destination folders: {exc}",
            progress=_progress("repack", 0, 1), phase=_label("repack"),
            level="error",
        )
        return
    for index, row in enumerate(outstanding, start=1):
        if _cancelled(ctx):
            return
        # Same as the Dedupe loop above: a retried item's detail carries an
        # "error" folded onto its plan (job_items_repo.mark), which is not
        # a Move field.
        plan = {k: v for k, v in row["detail"].items() if k != "error"}
        move = repack.Move(**plan)
        try:
            move_folder_ids = folder_ids
            if move.from_path not in folder_ids:
                # A stale or unresolved parent_path: fall back to Drive's
                # own record of this one file's current parent, exactly as
                # this row would resolve it — not cached for any other row,
                # since a shared parent_path is not a guarantee of a shared
                # parent (see Task 15's review, which caught that caching
                # bug for the same fallback in reorganize.py).
                parents = ctx.drive.get_file(move.drive_id).parents
                old_parent = parents[0] if parents else root.id
                move_folder_ids = {**folder_ids, move.from_path: old_parent}
            repack.apply_move(
                ctx.writer, ctx.conn, move, move_folder_ids, drive=ctx.drive
            )
        except (DriveError, KeyError) as exc:
            # KeyError is a backstop, not the primary path: an
            # unresolvable from_path should be caught by the fallback
            # above, but one file's stale catalog row must degrade to a
            # single failed item, never a bare exception that kills the
            # whole confirmed run and leaves every later item — including
            # the entire Sweep phase — unprocessed.
            items.mark(run_id, "repack", row["item_key"], "failed",
                       {"error": str(exc)})
            yield ProgressEvent(f"{move.name}: {exc}",
                                progress=_progress("repack", index,
                                                   len(outstanding)),
                                phase=_label("repack"), level="error",
                                done=index, total=len(outstanding))
            continue
        items.mark(run_id, "repack", row["item_key"], "done")
        yield ProgressEvent(
            f"{move.from_path} → {move.to_folder}/{move.new_name}",
            progress=_progress("repack", index, len(outstanding)),
            phase=_label("repack"), done=index, total=len(outstanding),
        )

    # ---- Sweep -------------------------------------------------------
    empty = repack.plan_sweep(ctx.drive, root.id)
    for index, (folder_id, name) in enumerate(empty, start=1):
        if _cancelled(ctx):
            return
        try:
            repack.apply_sweep(ctx.writer, folder_id)
        except DriveError as exc:
            # One folder Drive won't trash — already gone, permissions,
            # whatever — must not abort the run after Dedupe and Repack
            # have already written to Drive; same per-item treatment as
            # Repack's guard, above.
            items.put(run_id, "sweep", folder_id, run_id, "failed",
                      {"name": name, "error": str(exc)})
            yield ProgressEvent(
                f"{name}: {exc}",
                progress=_progress("sweep", index, len(empty)),
                phase=_label("sweep"), level="error",
                done=index, total=len(empty),
            )
            continue
        items.put(run_id, "sweep", folder_id, run_id, "done", {"name": name})
        yield ProgressEvent(
            f"Trashed the now-empty folder {name}.",
            progress=_progress("sweep", index, len(empty)),
            phase=_label("sweep"), done=index, total=len(empty),
        )

    yield ProgressEvent(
        f"Done. {len(items.all(run_id, 'dedupe', 'done'))} duplicate(s) "
        f"trashed, {len(items.all(run_id, 'repack', 'done'))} file(s) moved, "
        f"{len(items.all(run_id, 'sweep', 'done'))} folder(s) swept.",
        progress=1.0,
    )

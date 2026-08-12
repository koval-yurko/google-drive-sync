"""Get everything out of the archives and into the Global Photos folder.

Four cheap, read-only phases produce a plan; a fifth moves bytes. The gate
between them is `confirm`, and the plan survives in `media` and in
`job_items`, so confirming acts on exactly what was reported and a killed run
resumes instead of restarting.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions import (
    check_connection,
    organize,
    pair_metadata,
    plan_organize,
    scan_archives,
)
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.actions.phases import run_phase
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.media_repo import MediaRepo

ID = "sync_archives"
TITLE = "Sync from Archives"
DESCRIPTION = (
    "Extract every file from the ZIP archives into the Global Photos folder, "
    "skipping anything already there. Reports the plan and uploads nothing "
    "until you confirm."
)
ORDER = 1
GROUP = "flow"

PLAN_PHASE = "plan"
PLAN_ITEM = "planned"

_READ_ONLY = (
    ("Connect", (0.00, 0.02), check_connection),
    ("Scan", (0.02, 0.25), scan_archives),
    ("Pair", (0.25, 0.45), pair_metadata),
    ("Plan", (0.45, 0.55), plan_organize),
)
_TOTAL_PHASES = len(_READ_ONLY) + 1


class Params(ActionParams):
    confirm: bool = False
    run_id: str | None = None
    limit: int | None = None
    workers: int = 4
    retry_errors: bool = False


def _cancelled(ctx: ActionContext) -> bool:
    return ctx.cancelled is not None and ctx.cancelled.is_set()


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    items = JobItemsRepo(ctx.conn)
    run_id = ctx.run_id or "adhoc"

    if params.confirm and not items.all(run_id, PLAN_PHASE, "done"):
        yield ProgressEvent(
            "There is no plan for this run to confirm. Run Sync from Archives "
            "without confirm first, read what it reports, then confirm that "
            "run.",
            progress=1.0,
            level="error",
        )
        return

    for index, (name, span, module) in enumerate(_READ_ONLY, start=1):
        if _cancelled(ctx):
            return
        failed = False
        for event in run_phase(
            name, span, module.run, ctx, module.Params(),
            index=index, total=_TOTAL_PHASES,
        ):
            failed = failed or event.level == "error"
            yield event
        if failed:
            yield ProgressEvent(
                f"{name} failed; the flow stopped there. Fix the cause and "
                "resume this job.",
                progress=span[1],
                level="error",
            )
            return

    items.put(run_id, PLAN_PHASE, PLAN_ITEM, run_id, "done")

    summary = MediaRepo(ctx.conn).summary()
    yield ProgressEvent(
        f"{summary['pending']} file(s) to upload, {summary['skipped']} already "
        f"in the Global folder, {summary['errors']} in error. Open Review to "
        "see every file and where it would go.",
        progress=0.55,
    )

    if not params.confirm:
        yield ProgressEvent(
            "Nothing has been uploaded. Re-run with confirm to move the bytes.",
            progress=1.0,
        )
        return

    if _cancelled(ctx):
        return

    yield from run_phase(
        "Upload", (0.55, 1.00), organize.run, ctx,
        organize.Params(
            workers=params.workers,
            retry_errors=params.retry_errors,
            limit=params.limit,
        ),
        index=_TOTAL_PHASES, total=_TOTAL_PHASES,
    )

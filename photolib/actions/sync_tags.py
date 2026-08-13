"""Mirror the catalog's tags onto Drive's appProperties.

The catalog is the query engine — unlimited tags, instant filtering, no API
calls. Drive is the durable copy: one property per tag (`t_family` = `1`), so
tags survive the loss of this machine, travel with the file, and stay
queryable by anything built later.

Confirm-gated: it reports what it would change and does nothing until you
confirm.

The candidate set is every live file that has tags now, or had them last time
this ran. Without the second half, untagging a file would drop it out of the
set and leave its property on Drive forever. Drive itself is still read for
the diff, so a file edited elsewhere is reconciled correctly.
"""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.tags_repo import TagsRepo
from photolib.drive.errors import DriveError

ID = "sync_tags"
TITLE = "Sync Tags to Drive"
DESCRIPTION = (
    "Make each file's Drive appProperties match its tags in the catalog, "
    "adding what is missing and removing what you untagged. Reports what it "
    "would do unless you confirm."
)
ORDER = 60

PREFIX = "t_"

# Drive allows 30 appProperties per file and Organize already writes about
# five. Refusing at 25 turns an opaque API failure into a clear warning.
MAX_TAGS = 25


class Params(ActionParams):
    confirm: bool = False
    limit: int = 0
    """0 means every candidate."""


def _candidates(conn, limit: int) -> list:
    """Files with tags now, or tags written last time. Trashed ones excluded."""
    sql = (
        "SELECT drive_id, name, synced_tags FROM drive_files "
        "WHERE trashed_at IS NULL AND ("
        "  drive_id IN (SELECT drive_id FROM file_tags) "
        "  OR (synced_tags IS NOT NULL AND synced_tags != '')"
        ") ORDER BY parent_path, name"
    )
    if limit > 0:
        return list(conn.execute(f"{sql} LIMIT ?", (limit,)))
    return list(conn.execute(sql))


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    desired_by_file = TagsRepo(ctx.conn).slugs_by_file()
    rows = _candidates(ctx.conn, params.limit)
    if not rows:
        yield ProgressEvent(
            "No tagged files to sync. Tag something on the Library page first.",
            progress=1.0,
        )
        return

    yield ProgressEvent(f"Examining {len(rows)} file(s).", progress=0.0)

    plans: list[tuple[str, str, set[str], set[str], set[str]]] = []
    over_budget = 0
    for index, row in enumerate(rows, start=1):
        drive_id, name = row["drive_id"], row["name"]
        desired = desired_by_file.get(drive_id, set())

        if len(desired) > MAX_TAGS:
            over_budget += 1
            yield ProgressEvent(
                f"{name}: {len(desired)} tags exceeds the {MAX_TAGS} that fit in "
                f"Drive's appProperties. Skipping — remove some tags first.",
                level="warn",
            )
            continue

        try:
            current_props = ctx.drive.app_properties(drive_id)
        except DriveError as exc:
            yield ProgressEvent(f"{name}: cannot read properties: {exc}",
                                level="error")
            continue

        current = {
            key[len(PREFIX):] for key in current_props if key.startswith(PREFIX)
        }
        adds, removes = desired - current, current - desired
        if adds or removes:
            plans.append((drive_id, name, desired, adds, removes))

        if index % 50 == 0:
            yield ProgressEvent(
                f"Examined {index} of {len(rows)}.",
                progress=0.5 * index / len(rows),
            )

    if not plans:
        yield ProgressEvent(
            f"0 file(s) to change — Drive already matches the catalog."
            + (f" {over_budget} skipped as over budget." if over_budget else ""),
            progress=1.0,
        )
        return

    added = sum(len(plan[3]) for plan in plans)
    removed = sum(len(plan[4]) for plan in plans)
    yield ProgressEvent(
        f"{len(plans)} file(s) differ: {added} tag(s) to add, "
        f"{removed} to remove.",
        progress=0.5,
    )
    # The Drive id rides along in the report: names repeat across months, and
    # the id is the only thing that identifies the file being changed.
    for drive_id, name, _, adds, removes in plans[:50]:
        for slug in sorted(adds):
            yield ProgressEvent(f"would add {PREFIX}{slug} to {name} ({drive_id})")
        for slug in sorted(removes):
            yield ProgressEvent(
                f"would remove {PREFIX}{slug} from {name} ({drive_id})"
            )

    if not params.confirm:
        yield ProgressEvent(
            f"Report only — Drive was not changed. Re-run with confirm to "
            f"update {len(plans)} file(s).",
            progress=1.0,
            level="warn",
        )
        return

    changed = 0
    for index, (drive_id, name, desired, adds, removes) in enumerate(plans, start=1):
        properties: dict[str, str | None] = {
            f"{PREFIX}{slug}": "1" for slug in adds
        }
        # None is how the Drive API deletes a property.
        properties.update({f"{PREFIX}{slug}": None for slug in removes})
        try:
            ctx.writer.update_properties(drive_id, properties)
        except DriveError as exc:
            yield ProgressEvent(f"{name}: {exc}", level="error")
            continue

        ctx.conn.execute(
            "UPDATE drive_files SET synced_tags = ? WHERE drive_id = ?",
            (",".join(sorted(desired)), drive_id),
        )
        ctx.conn.commit()
        changed += 1
        if index % 20 == 0:
            yield ProgressEvent(
                f"Updated {index} of {len(plans)}.",
                progress=0.5 + 0.5 * index / len(plans),
            )

    yield ProgressEvent(
        f"Updated {changed} file(s) on Drive: {added} tag(s) added, "
        f"{removed} removed.",
        progress=1.0,
    )

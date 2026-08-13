"""Verify Drive access and report on the configured folders."""

from __future__ import annotations

from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE
from photolib.drive.errors import DriveError

ID = "check_connection"
TITLE = "Check Connection"
DESCRIPTION = (
    "Verify that Drive credentials work and that the configured "
    "Global Photos and ZIP source folders are reachable."
)
ORDER = 0


class Params(ActionParams):
    pass


_CHECKS = ((PHOTOS_ROOT, "Global Photos folder"), (ZIP_SOURCE, "ZIP source folder"))


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    total = len(_CHECKS)
    for index, (key, label) in enumerate(_CHECKS, start=1):
        progress = index / total
        folder = ctx.settings.get_folder(key)
        if folder is None:
            yield ProgressEvent(
                f"{label} is not configured.", progress=progress, level="warn"
            )
            continue

        try:
            found = ctx.drive.get_file(folder.id)
        except DriveError as exc:
            yield ProgressEvent(
                f"{label} '{folder.name}' is unreachable: {exc}",
                progress=progress,
                level="error",
            )
            continue

        children = ctx.drive.list_children(folder.id)
        archives = [c for c in children if c.name.lower().endswith(".zip")]
        detail = f"{len(children)} item(s)"
        if key == ZIP_SOURCE:
            detail += f", {len(archives)} archive(s)"
        yield ProgressEvent(
            f"{label} '{found.name}' is reachable: {detail}.", progress=progress
        )

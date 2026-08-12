"""Move every planned file from the archives into the destination.

The only mutating action in the pipeline. Per file: range-read the entry,
inflate it to a temp file, verify its CRC32, upload it resumably, and compare
Drive's MD5 against one computed locally. Nothing half-succeeds silently.

Interrupted runs resume: a session URI is recorded the moment it exists, and
finished files are never revisited. Duplicates are uploaded like anything
else — detection is recorded, but nothing is ever withheld.

Threading rule: workers do network and disk only. Every SQLite write happens
on this generator's thread, fed by a queue the workers push to.
"""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT
from photolib.db.scan_repo import ScanRepo
from photolib.downloads import InflightRegistry, run_folder_name, sweep_empty
from photolib.drive.errors import DriveError
from photolib.transfer import TransferError, mime_for, transfer_entry
from photolib.ziparchive.reader import ZipEntry

ID = "organize"
TITLE = "Organize Photos"
DESCRIPTION = (
    "Upload every planned file into its destination bucket folder, verifying "
    "each one against its CRC32 before upload and Drive's MD5 after. Safe to "
    "re-run: finished files are skipped and interrupted ones resume."
)
ORDER = 40

# Drive allows 124 bytes for a property's key and value combined.
_MAX_PROPERTY = 100


class Params(ActionParams):
    workers: int = 4
    retry_errors: bool = False
    limit: int | None = None


def _entry_of(row) -> ZipEntry:
    return ZipEntry(
        path=row["path"],
        name=row["name"],
        crc32=row["crc32"],
        size=row["size"],
        compressed_size=row["compressed_size"],
        method=row["method"],
        local_header_offset=row["local_header_offset"],
    )


def _properties(row) -> dict[str, str]:
    """Metadata to store on the Drive file. No tags — those are Phase 4."""
    props: dict[str, str] = {
        "source_archive": row["archive_name"],
        "source_crc": str(row["crc32"]),
    }
    if row["capture_time"] is not None:
        props["capture_time"] = datetime.fromtimestamp(
            row["capture_time"], tz=timezone.utc
        ).isoformat()
    if row["country"]:
        props["country"] = row["country"]
    return {k: v[:_MAX_PROPERTY] for k, v in props.items()}


def _prepare_folders(ctx: ActionContext, root_id: str, rows) -> dict[str, str]:
    """Create every destination bucket folder up front, sequentially.

    Drive permits two folders with the same name in the same parent, so a
    concurrent ensure_folder would not fail loudly — it would quietly split a
    month across two folders. Doing this before the pool starts removes the
    race entirely.
    """
    cache: dict[str, str] = {}
    for folder in sorted({row["target_folder"] for row in rows}):
        cache[folder] = ctx.writer.ensure_folder(root_id, folder).id
    return cache


def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    repo = MediaRepo(ctx.conn)
    scans = ScanRepo(ctx.conn)

    if ctx.writer is None:
        yield ProgressEvent(
            "This context cannot write to Drive.", progress=1.0, level="error"
        )
        return

    photos_root = ctx.settings.get_folder(PHOTOS_ROOT)
    if photos_root is None:
        yield ProgressEvent(
            "The Global Photos folder must be configured in Settings first.",
            progress=1.0,
            level="error",
        )
        return

    summary = repo.summary()
    if summary["unplanned"]:
        yield ProgressEvent(
            f"{summary['unplanned']} file(s) have no destination. Run Plan "
            "Organization first — Organize executes a plan, it never invents one.",
            progress=1.0,
            level="error",
        )
        return

    rows = repo.pending_uploads(
        retry_errors=params.retry_errors, limit=params.limit
    )
    if not rows:
        yield ProgressEvent(
            f"Nothing to upload. {summary['uploaded']} file(s) already done.",
            progress=1.0,
        )
        return

    downloads_root = Path(ctx.config.downloads_dir)
    run_dir = downloads_root / run_folder_name(datetime.now())
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        yield ProgressEvent(
            f"Cannot use the downloads folder {downloads_root}: {exc}",
            progress=1.0,
            level="error",
        )
        return

    # Another run's leftovers are evidence, not litter. Empty folders say
    # nothing, so they go; folders holding bytes are reported and left alone.
    for stale in sweep_empty(downloads_root, keep=run_dir):
        yield ProgressEvent(
            f"An earlier run left {stale['files']} unfinished file(s), "
            f"{stale['bytes'] / 1e9:.2f} GB, in downloads/{stale['dir']}/."
        )

    # A private registry when nobody supplied one, so the reporting below has
    # no None to step around.
    inflight = ctx.inflight or InflightRegistry()
    inflight.open_run(run_dir)

    try:
        folders = _prepare_folders(ctx, photos_root.id, rows)
    except DriveError as exc:
        yield ProgressEvent(
            f"Cannot create destination folders: {exc}", progress=1.0, level="error"
        )
        return

    total_bytes = sum(row["size"] for row in rows) or 1
    done_bytes = 0
    uploaded = failed = 0
    sessions: queue.Queue[tuple[int, str]] = queue.Queue()

    yield ProgressEvent(
        f"Uploading {len(rows)} file(s), {total_bytes / 1e9:.2f} GB, "
        f"across {params.workers} worker(s).",
        progress=0.0,
    )

    def move(row):
        """Runs on a worker thread. No database access here."""
        entry = _entry_of(row)
        archive_id = row["archive_drive_id"]
        key = str(row["entry_id"])
        destination = f"{photos_root.name}/{row['target_folder']}"
        try:
            return transfer_entry(
                read_range=lambda s, e: ctx.drive.read_range(archive_id, s, e),
                entry=entry,
                writer=ctx.writer,
                parent_id=folders[row["target_folder"]],
                name=row["target_name"],
                properties=_properties(row),
                spool_dir=run_dir,
                session_uri=row["upload_session_uri"],
                on_session=lambda uri: sessions.put((row["entry_id"], uri)),
                on_spool=lambda path: inflight.start(
                    key,
                    name=row["target_name"],
                    destination=destination,
                    expected_size=row["size"],
                    path=path,
                ),
                on_progress=lambda offset: inflight.uploaded(key, offset),
                skip_if_md5=(
                    row["match_md5"] if row["plan_verdict"] == "verify" else None
                ),
                adopt_id=(
                    row["plan_match"] if row["plan_verdict"] == "verify" else None
                ),
            )
        finally:
            inflight.finish(key)

    def drain_sessions() -> None:
        while True:
            try:
                entry_id, uri = sessions.get_nowait()
            except queue.Empty:
                return
            repo.save_session(entry_id, uri)

    with ThreadPoolExecutor(max_workers=max(1, params.workers)) as pool:
        futures = {pool.submit(move, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            drain_sessions()
            try:
                result = future.result()
            except TransferError as exc:
                repo.mark_failed(row["entry_id"], f"{exc.stage}: {exc.reason}")
                failed += 1
                level = "error"
                message = f"{row['name']}: {exc.reason}"
            except DriveError as exc:
                repo.mark_failed(row["entry_id"], f"drive: {exc}")
                failed += 1
                level = "error"
                message = f"{row['name']}: {exc}"
            else:
                repo.mark_uploaded(
                    row["entry_id"], result.drive_file_id, result.md5
                )
                if result.adopted:
                    message = (
                        f"{row['name']}: already in Drive, verified by MD5 — "
                        "not uploaded."
                    )
                else:
                    # The Library browses drive_files; record the arrival so it
                    # is visible without waiting for the next Scan.
                    scans.record_drive_file(
                        drive_id=result.drive_file_id,
                        name=row["target_name"],
                        parent_path=row["target_folder"],
                        md5=result.md5,
                        size=result.size,
                        mime_type=mime_for(row["target_name"]),
                    )
                    message = f"{row['target_folder']}/{row['target_name']}"
                uploaded += 1
                level = "info"

            done_bytes += row["size"]
            yield ProgressEvent(
                message, progress=min(done_bytes / total_bytes, 1.0), level=level
            )

    drain_sessions()
    inflight.close_run()
    leftover = [f for f in run_dir.iterdir() if f.is_file()]
    if leftover:
        yield ProgressEvent(
            f"{len(leftover)} unfinished file(s) left in "
            f"downloads/{run_dir.name}/."
        )
    else:
        run_dir.rmdir()

    detail = f"Uploaded {uploaded} file(s)."
    if failed:
        detail += (
            f" {failed} failed and are marked for retry — re-run with "
            "retry_errors to try them again."
        )
    yield ProgressEvent(detail, progress=1.0, level="warn" if failed else "info")

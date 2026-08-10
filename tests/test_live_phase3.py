"""Opt-in end-to-end write test against the real Drive account.

Run with:
    PHOTOLIB_HOME=/path/to/repo uv run pytest -m live tests/test_live_phase3.py -v

Uploads the three smallest media files in the export into a throwaway folder,
verifies them, and trashes everything it created. It never writes into a
destination month folder and never touches an existing file.
"""

from __future__ import annotations

import hashlib

import pytest

from photolib import transfer
from photolib.actions.base import ActionContext
from photolib.actions.pair_metadata import Params as PairParams
from photolib.actions.pair_metadata import run as pair
from photolib.actions.plan_organize import Params as PlanParams
from photolib.actions.plan_organize import run as plan
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient
from photolib.drive.writer import DriveWriter
from photolib.ziparchive.reader import ZipEntry

pytestmark = pytest.mark.live

SCRATCH_FOLDER = "phase3-live-check"


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    cfg = Config.load()
    if not cfg.token_path.exists():
        pytest.skip("token.json not present")

    conn = catalog.connect(tmp_path_factory.mktemp("live") / "live.db")
    settings = SettingsRepo(conn)
    real = SettingsRepo(catalog.connect(cfg.db_path))
    for key in ("photos_root", "zip_source"):
        folder = real.get_folder(key)
        if folder is None:
            pytest.skip(f"{key} is not configured; set it in Settings first")
        settings.set_folder(key, folder)

    drive = DriveClient(TokenProvider(cfg.credentials_path, cfg.token_path))
    writer = DriveWriter(drive)
    ctx = ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=writer
    )
    list(scan(ctx, ScanParams()))
    list(pair(ctx, PairParams()))
    list(plan(ctx, PlanParams()))

    root = settings.get_folder(PHOTOS_ROOT)
    scratch = writer.ensure_folder(root.id, SCRATCH_FOLDER)
    yield ctx, writer, scratch, tmp_path_factory.mktemp("spool")

    for child in drive.list_children(scratch.id):
        writer.trash(child.id)
    writer.trash(scratch.id)
    drive.close()


def test_three_smallest_files_move_and_verify(live):
    ctx, writer, scratch, spool = live
    rows = sorted(MediaRepo(ctx.conn).pending_uploads(), key=lambda r: r["size"])[:3]
    assert len(rows) == 3, "the export should hold at least three media files"

    for row in rows:
        entry = ZipEntry(
            path=row["path"], name=row["name"], crc32=row["crc32"],
            size=row["size"], compressed_size=row["compressed_size"],
            method=row["method"], local_header_offset=row["local_header_offset"],
        )
        archive_id = row["archive_drive_id"]
        result = transfer.transfer_entry(
            read_range=lambda s, e: ctx.drive.read_range(archive_id, s, e),
            entry=entry,
            writer=writer,
            parent_id=scratch.id,
            name=row["target_name"],
            properties={"source_archive": row["archive_name"],
                        "source_crc": str(row["crc32"])},
            spool_dir=spool,
        )
        uploaded = ctx.drive.get_file(result.drive_file_id)
        assert uploaded.md5 == result.md5, f"{row['name']} differs in Drive"
        assert uploaded.size == row["size"]


def test_the_scratch_folder_holds_exactly_what_we_put_there(live):
    ctx, _, scratch, _ = live
    assert len(ctx.drive.list_children(scratch.id)) == 3


def test_a_resumed_session_reports_a_real_offset(live):
    """The resume path, against the real API rather than the fake."""
    ctx, writer, scratch, spool = live
    row = min(MediaRepo(ctx.conn).pending_uploads(), key=lambda r: r["size"])
    session = writer.start_session(
        parent_id=scratch.id, name="resume-probe.bin", size=row["size"],
        mime_type="application/octet-stream", properties={},
    )
    assert writer.session_offset(session, row["size"]) == 0

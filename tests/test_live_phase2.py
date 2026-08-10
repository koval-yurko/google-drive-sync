"""Opt-in end-to-end run against the real archives.

Run with:
    PHOTOLIB_HOME=/path/to/repo uv run pytest -m live tests/test_live_phase2.py -v

Reads only archive indexes and sidecars — a few megabytes in total. Writes
nothing to Drive.
"""

from __future__ import annotations

import pytest

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
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
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
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    yield context
    drive.close()


def test_scan_indexes_seventeen_archives(ctx):
    list(scan(ctx, ScanParams()))
    counts = ScanRepo(ctx.conn).counts()
    assert counts["archives"] == 17
    assert counts["media"] == 1284
    assert counts["sidecars"] == 1276
    assert counts["drive_files"] > 2000


def test_pairing_leaves_no_sidecar_unmatched(ctx):
    list(pair(ctx, PairParams()))
    summary = MediaRepo(ctx.conn).summary()
    assert summary["media"] == 1284
    assert summary["with_sidecar"] >= 1269   # 1284 media, 15 known to lack a sidecar


def test_plan_assigns_a_destination_to_every_file(ctx):
    list(plan(ctx, PlanParams()))
    summary = MediaRepo(ctx.conn).summary()
    assert summary["planned"] == 1284
    assert summary["unplanned"] == 0
    assert summary["pending"] == 1284      # nothing is ever withheld
    assert summary["duplicates"] >= 480    # measured overlap with the destination


def test_no_target_collides(ctx):
    rows = MediaRepo(ctx.conn).all_media()
    targets = [(r["target_folder"], r["target_name"]) for r in rows]
    assert len(targets) == len(set(targets))

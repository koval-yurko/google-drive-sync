"""Regression coverage for the dedupe summary line and its Drive-call cost.

`clear_duplicates.run`'s opening line always reported the total number of
files the walk scanned, not just the number found redundant. Task 15's
first draft dropped that count because `dedupe.Removal` didn't carry
enough information to reconstruct it without walking Drive a second time,
and computed freed bytes with one extra `get_file` call per removal — a
failure surface a report-only run never had before, since once the walk
itself succeeded nothing else could fail. The plan was amended so
`plan_removals` returns the scanned total as a third element and
`Removal` carries `size`, restoring the exact original wording and the
freed-bytes figure with no extra Drive calls anywhere in the reporting
path — verified here by making any `get_file` call blow up.
"""

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.clear_duplicates import Params, run
from photolib.config import Config
from photolib.db import catalog
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)

    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("m1", "2023-11", parent="photos")
    # Two identical copies (a redundant pair) plus one unrelated file — the
    # scanned total (3) must differ from the redundant count (1).
    drive.add_file("a1", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("a2", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("u1", "IMG_2.MOV", b"unique bytes", parent="m1")
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))

    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=drive
    )


def test_the_summary_line_reports_the_total_scanned(ctx):
    events = list(run(ctx, Params()))
    text = " ".join(e.message for e in events)
    assert "3 file(s) scanned: 1 redundant copy in 1 group(s)" in text


def test_a_report_only_run_never_calls_get_file(ctx):
    def _boom(*_args, **_kwargs):
        raise AssertionError("a report-only run should never call get_file")

    ctx.drive.get_file = _boom

    events = list(run(ctx, Params()))

    assert events[-1].level != "error"


def test_a_confirmed_batch_shares_one_trashed_at_stamp(tmp_path, monkeypatch):
    """`dedupe.apply_removal` accepts an explicit stamp (`None` means "now");
    the action must compute one stamp per confirmed run and pass it to
    every removal in the batch, not let each call default to its own."""
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)

    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("m1", "2023-11", parent="photos")
    # Two independent duplicate groups, so two separate removals land in
    # the same confirmed batch.
    drive.add_file("a1", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("a2", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("b1", "IMG_2.HEIC", b"other bytes", parent="m1")
    drive.add_file("b2", "IMG_2.HEIC", b"other bytes", parent="m1")
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    ctx = ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=drive
    )
    # Pre-catalogue the copies expected to be trashed (the plain,
    # unverified tiebreak keeps the first-added file of each pair) so the
    # UPDATE has a row to stamp.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path) VALUES "
        "('a2', 'IMG_1.HEIC', '2023-11'), ('b2', 'IMG_2.HEIC', '2023-11')"
    )
    conn.commit()

    list(run(ctx, Params(confirm=True)))

    rows = conn.execute(
        "SELECT drive_id, trashed_at FROM drive_files WHERE trashed_at IS NOT NULL"
    ).fetchall()
    assert {row["drive_id"] for row in rows} == {"a2", "b2"}
    assert len({row["trashed_at"] for row in rows}) == 1

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.scan_archives import Params, run
from photolib.config import Config
from photolib.db import catalog
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2022/IMG_1.HEIC": b"heic-bytes",
    "Takeout/Google Photos/Photos from 2022/IMG_1.HEIC.supplemental-metadata.json":
        b'{"title": "IMG_1.HEIC"}',
    "Takeout/Google Photos/Lake Como/IMG_2.MOV": b"mov-bytes",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)

    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(ARCHIVE), parent="zips")
    drive.add_folder("photos", "Photos")
    drive.add_folder("back", "back_2024_01", parent="photos")
    drive.add_file("d1", "IMG_1.HEIC", b"existing-bytes", parent="back")

    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    return ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)


def drain(ctx):
    return [event.message for event in run(ctx, Params())]


def test_indexes_every_entry_with_its_kind(ctx):
    drain(ctx)
    counts = ScanRepo(ctx.conn).counts()
    assert counts["archives"] == 1
    assert counts["media"] == 2
    assert counts["sidecars"] == 1


def test_indexes_the_destination_folder(ctx):
    drain(ctx)
    by_name = ScanRepo(ctx.conn).drive_file_names()
    assert by_name["IMG_1.HEIC"][0]["parent_path"] == "back_2024_01"


def test_indexes_the_destination_at_any_depth(ctx):
    ctx.drive.add_folder("sub", "sub", parent="back")
    ctx.drive.add_file("d2", "DEEP.JPG", b"deep-bytes", parent="sub")
    drain(ctx)
    by_name = ScanRepo(ctx.conn).drive_file_names()
    assert by_name["DEEP.JPG"][0]["parent_path"] == "back_2024_01/sub"


def test_rerun_skips_unchanged_archives(ctx):
    drain(ctx)
    messages = drain(ctx)
    assert any("unchanged" in m for m in messages)
    assert ScanRepo(ctx.conn).counts()["entries"] == 3


def test_no_archives_still_refreshes_the_destination_index(ctx):
    ctx.drive.trash("z1")
    messages = drain(ctx)
    assert any("No archives" in m for m in messages)
    by_name = ScanRepo(ctx.conn).drive_file_names()
    assert by_name["IMG_1.HEIC"][0]["parent_path"] == "back_2024_01"


def test_reports_missing_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    ctx = ActionContext(
        conn=conn, drive=FakeDrive(), settings=SettingsRepo(conn), config=cfg
    )
    events = list(run(ctx, Params()))
    assert any(e.level == "error" for e in events)


def test_run_is_a_generator():
    import inspect

    assert inspect.isgeneratorfunction(run)


def test_scan_records_a_capture_hint_for_destination_files(ctx):
    ctx.drive.add_file(
        "hinted", "IMG_7.HEIC", b"x", parent="photos",
        mime_type="image/heic", modified_time="2024-01-13T10:00:00Z",
    )
    list(run(ctx, Params()))
    row = ctx.conn.execute(
        "SELECT capture_hint FROM drive_files WHERE drive_id = 'hinted'"
    ).fetchone()
    assert row["capture_hint"] == 1705140000

import threading

import pytest

from photolib.actions import sync_archives
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db import catalog
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC":
        b"pretend this is a photograph" * 100,
}


@pytest.fixture
def sync_context(tmp_path, monkeypatch, conn):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    cfg = Config.load()
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(ARCHIVE), parent="zips")
    drive.add_folder("photos", "Photos")
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    # The fake speaks both halves of the interface, so it is drive and writer.
    return ActionContext(
        conn=conn,
        drive=drive,
        settings=settings,
        config=cfg,
        writer=drive,
        run_id="run-test",
        cancelled=threading.Event(),
    )


@pytest.fixture
def sync_context_without_folders(tmp_path, monkeypatch, conn):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    cfg = Config.load()
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    return ActionContext(
        conn=conn,
        drive=drive,
        settings=settings,
        config=cfg,
        writer=drive,
        run_id="run-test",
        cancelled=threading.Event(),
    )


def test_unconfirmed_run_stops_before_uploading(sync_context, conn):
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    messages = " ".join(e.message for e in events)
    assert "Re-run with confirm" in messages
    assert not any(e.phase and e.phase.startswith("Upload") for e in events)


def test_unconfirmed_run_records_that_a_plan_exists(sync_context, conn):
    list(sync_archives.run(sync_context, sync_archives.Params()))
    items = JobItemsRepo(conn).all(sync_context.run_id, "plan")
    assert [(i["item_key"], i["state"]) for i in items] == [("planned", "done")]


def test_confirmed_run_without_a_plan_refuses(sync_context, conn):
    events = list(sync_archives.run(
        sync_context, sync_archives.Params(confirm=True)
    ))
    assert events[-1].level == "error"
    assert "no plan" in events[-1].message.lower()


def test_confirmed_run_after_a_plan_reaches_the_upload_phase(
    sync_context, conn
):
    list(sync_archives.run(sync_context, sync_archives.Params()))
    events = list(sync_archives.run(
        sync_context, sync_archives.Params(confirm=True)
    ))
    assert any(e.phase and e.phase.startswith("Upload") for e in events)


def test_progress_is_monotonic_and_bounded(sync_context):
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    values = [e.progress for e in events if e.progress is not None]
    assert values == sorted(values)
    assert 0.0 <= values[0] and values[-1] <= 1.0


def test_a_fatal_phase_error_ends_the_flow(sync_context_without_folders):
    events = list(sync_archives.run(
        sync_context_without_folders, sync_archives.Params()
    ))
    assert events[-1].level == "error"
    assert not any(e.phase and e.phase.startswith("Pair") for e in events)


def test_cancellation_between_phases_stops_the_flow(sync_context):
    sync_context.cancelled.set()
    events = list(sync_archives.run(sync_context, sync_archives.Params()))
    assert not any(e.phase and e.phase.startswith("Plan") for e in events)

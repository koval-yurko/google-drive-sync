import pytest

from photolib.actions import check_connection
from photolib.actions.base import ActionContext, ProgressEvent
from photolib.actions.registry import UnknownActionError, all_actions, get_action
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


def make_ctx(conn, drive) -> ActionContext:
    return ActionContext(
        conn=conn, drive=drive, settings=SettingsRepo(conn), config=Config.load()
    )


def test_registry_discovers_check_connection():
    ids = [spec.id for spec in all_actions()]
    assert "check_connection" in ids


def test_registry_specs_are_complete():
    spec = get_action("check_connection")
    assert spec.title
    assert spec.description
    assert spec.json_schema()["type"] == "object"


def test_registry_rejects_unknown_action():
    with pytest.raises(UnknownActionError):
        get_action("no_such_action")


def test_check_connection_reports_missing_settings(conn):
    drive = FakeDrive()
    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "not configured" in text.lower()
    assert any(e.level == "warn" for e in events)


def test_check_connection_reports_configured_folders(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "Global Photos")
    drive.add_folder("zips", "zip-3-22-26")
    drive.add_file("z1", "takeout-001.zip", build_zip({"a.txt": b"x"}), parent="zips")
    drive.add_file("z2", "takeout-002.zip", build_zip({"b.txt": b"y"}), parent="zips")

    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Global Photos"))
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-3-22-26"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "Global Photos" in text
    assert "2 archive" in text
    assert all(e.level != "error" for e in events)


def test_check_connection_reports_unreachable_folder(conn):
    drive = FakeDrive()
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="ghost", name="Gone"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    assert any(e.level == "error" for e in events)


def test_progress_is_monotonic_and_bounded(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "P")
    drive.add_folder("zips", "Z")
    SettingsRepo(conn).set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="P"))
    SettingsRepo(conn).set_folder(ZIP_SOURCE, FolderRef(id="zips", name="Z"))

    values = [
        e.progress
        for e in check_connection.run(make_ctx(conn, drive), check_connection.Params())
        if e.progress is not None
    ]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)

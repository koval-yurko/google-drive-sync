import pytest

from photolib.actions.base import ActionContext
from photolib.actions.clear_stale_trees import Params, run
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
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
    drive.add_folder("tree", "Takeout")
    drive.add_folder("sub", "Google Photos", parent="tree")
    drive.add_file("s1", "IMG_1.HEIC", b"stale one", parent="sub")
    drive.add_file("s2", "IMG_2.MOV", b"stale two", parent="sub")
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))

    conn.execute("INSERT INTO archives (drive_id, name, size) VALUES ('z1','a.zip',9)")
    conn.executemany(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind)"
        " VALUES (1,?,?,1,9,5,8,0,'media')",
        [("d/IMG_1.HEIC", "IMG_1.HEIC"), ("d/IMG_2.MOV", "IMG_2.MOV")],
    )
    conn.commit()
    repo = MediaRepo(conn)
    repo.upsert_media(1)
    repo.upsert_media(2)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    repo.set_plan(2, target_folder="2019-01", target_name="IMG_2.MOV")
    # Only the first has actually been uploaded and verified.
    repo.mark_uploaded(1, drive_file_id="up1", md5="abc")

    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=drive
    )


def test_a_missing_folder_id_is_refused(ctx):
    events = list(run(ctx, Params()))
    assert events[-1].level == "error"
    assert ctx.drive.trashed == []


def test_the_default_run_is_a_report_and_trashes_nothing(ctx):
    events = list(run(ctx, Params(tree_folder_id="tree")))
    assert ctx.drive.trashed == []
    text = " ".join(e.message for e in events)
    assert "IMG_1.HEIC" in text
    assert "confirm" in text.lower()


def test_only_verified_uploads_are_eligible(ctx):
    events = list(run(ctx, Params(tree_folder_id="tree")))
    text = " ".join(e.message for e in events)
    assert "1 eligible" in text
    assert "1 ineligible" in text


def test_confirming_trashes_only_the_eligible_file(ctx):
    list(run(ctx, Params(tree_folder_id="tree", confirm=True)))
    assert ctx.drive.trashed == ["s1"]
    assert {f.name for f in ctx.drive.list_children("sub")} == {"IMG_2.MOV"}


def test_trashing_is_recorded_in_the_catalog(ctx):
    ctx.conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path) "
        "VALUES ('s1', 'IMG_1.HEIC', 'Takeout/Google Photos')"
    )
    ctx.conn.commit()
    list(run(ctx, Params(tree_folder_id="tree", confirm=True)))
    row = ctx.conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = 's1'"
    ).fetchone()
    assert row["trashed_at"] is not None


def test_it_walks_nested_folders(ctx):
    ctx.drive.add_folder("deep", "Photos from 2023", parent="sub")
    ctx.drive.add_file("s3", "IMG_1.HEIC", b"deeper copy", parent="deep")
    list(run(ctx, Params(tree_folder_id="tree", confirm=True)))
    assert set(ctx.drive.trashed) == {"s1", "s3"}


def test_it_refuses_to_touch_the_destination_itself(ctx):
    events = list(run(ctx, Params(tree_folder_id="photos", confirm=True)))
    assert events[-1].level == "error"
    assert ctx.drive.trashed == []

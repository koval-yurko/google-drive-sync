import pytest

from photolib.actions.base import ActionContext
from photolib.actions.clear_duplicates import Params, run
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)

    from tests.fakes.fake_drive import FakeDrive

    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("m1", "2023-11", parent="photos")
    drive.add_folder("m2", "2024-01", parent="photos")
    # One photo in three copies across two folders, one unique file.
    drive.add_file("a1", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("a2", "IMG_1.HEIC", b"same bytes", parent="m1")
    drive.add_file("a3", "IMG_1 copy.HEIC", b"same bytes", parent="m2")
    drive.add_file("u1", "IMG_2.MOV", b"unique bytes", parent="m2")
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))

    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=drive
    )


def test_an_unconfigured_root_is_refused(ctx):
    ctx.conn.execute("DELETE FROM settings")
    ctx.conn.commit()
    events = list(run(ctx, Params()))
    assert events[-1].level == "error"
    assert ctx.drive.trashed == []


def test_the_default_run_is_a_report_and_trashes_nothing(ctx):
    events = list(run(ctx, Params()))
    assert ctx.drive.trashed == []
    text = " ".join(e.message for e in events)
    assert "2 redundant" in text
    assert "confirm" in text.lower()


def test_confirming_keeps_exactly_one_copy_per_group(ctx):
    list(run(ctx, Params(confirm=True)))
    assert len(ctx.drive.trashed) == 2
    survivors = {f.name for f in ctx.drive.list_children("m1")} | {
        f.name for f in ctx.drive.list_children("m2")
    }
    assert "IMG_2.MOV" in survivors
    remaining = [
        id for id in ("a1", "a2", "a3") if id not in ctx.drive.trashed
    ]
    assert len(remaining) == 1


def test_a_verified_upload_is_the_copy_that_survives(ctx):
    ctx.conn.execute("INSERT INTO archives (drive_id, name, size) VALUES ('z','a.zip',9)")
    ctx.conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind)"
        " VALUES (1,'d/IMG_1.HEIC','IMG_1.HEIC',1,9,5,8,0,'media')"
    )
    ctx.conn.commit()
    repo = MediaRepo(ctx.conn)
    repo.upsert_media(1)
    # The pipeline uploaded and verified the copy in 2024-01.
    repo.mark_uploaded(1, drive_file_id="a3", md5="whatever")

    list(run(ctx, Params(confirm=True)))
    assert set(ctx.drive.trashed) == {"a1", "a2"}


def test_zero_byte_files_are_reported_but_left_alone(ctx):
    ctx.drive.add_file("e1", "IMG_9.HEIC", b"", parent="m1")
    ctx.drive.add_file("e2", "IMG_8.HEIC", b"", parent="m2")
    events = list(run(ctx, Params(confirm=True)))
    assert "e1" not in ctx.drive.trashed
    assert "e2" not in ctx.drive.trashed
    text = " ".join(e.message for e in events)
    assert "2 zero-byte" in text


def test_a_clean_library_reports_nothing_to_do(ctx):
    for id in ("a2", "a3"):
        ctx.drive.trash(id)
    events = list(run(ctx, Params(confirm=True)))
    assert ctx.drive.trashed == ["a2", "a3"]
    assert "Nothing to do" in events[-1].message


def test_trashing_is_recorded_in_the_catalog(ctx):
    ctx.conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path) "
        "VALUES ('a2', 'IMG_1.HEIC', '2023-11')"
    )
    ctx.conn.commit()
    list(run(ctx, Params(confirm=True)))
    row = ctx.conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = 'a2'"
    ).fetchone()
    assert row["trashed_at"] is not None


def test_a_read_only_context_is_refused(ctx):
    ctx.writer = None
    events = list(run(ctx, Params(confirm=True)))
    assert events[-1].level == "error"
    assert ctx.drive.trashed == []

import pytest

from photolib.actions import reorganize
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive

JAN_2024 = 1704067200          # 2024-01-01T00:00:00Z
DAY = 86400


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("photos", "Photos")
    fake.add_folder("f-old", "2023-12", parent="photos")
    fake.add_folder("f-back", "back_2024_01", parent="photos")
    fake.add_folder("f-empty", "2022-04", parent="photos")
    fake.add_file("d1", "IMG_1.HEIC", b"a", parent="f-old")
    fake.add_file("d2", "IMG_2.HEIC", b"b", parent="f-back")
    fake.add_file("d3", "IMG_1.HEIC", b"c", parent="f-back")
    return fake


@pytest.fixture
def ctx(conn, drive, tmp_path):
    # d1 is catalogued (misfiled in 2023-12); d2 and d3 are legacy files
    # dated only by their capture hints. All three belong in 2024-01.
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind) VALUES"
        " (1, 'p/IMG_1.HEIC', 'IMG_1.HEIC', 1, 1, 1, 8, 0, 'media')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, capture_time, target_folder, target_name,"
        " upload_status, drive_file_id)"
        " VALUES (1, ?, '2023-12', 'IMG_1.HEIC', 'done', 'd1')",
        (JAN_2024,),
    )
    files = [
        ("d1", "IMG_1.HEIC", "2023-12", "aaaaaa11", None),
        ("d2", "IMG_2.HEIC", "back_2024_01", "bbbbbb22", JAN_2024 + DAY),
        ("d3", "IMG_1.HEIC", "back_2024_01", "cccccc33", JAN_2024 + 2 * DAY),
    ]
    for drive_id, name, parent, md5, hint in files:
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
            " mime_type, capture_hint)"
            " VALUES (?, ?, ?, ?, 1, 'image/heic', ?)",
            (drive_id, name, parent, md5, hint),
        )
    conn.commit()
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )
    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=config, writer=drive,
    )


def _run(ctx, **params) -> list:
    return list(reorganize.run(ctx, reorganize.Params(**params)))


def _parent_names(drive, *file_ids) -> set[str]:
    return {
        drive.get_file(drive.get_file(fid).parents[0]).name for fid in file_ids
    }


def test_declares_itself_to_the_registry():
    assert reorganize.ID == "reorganize"
    assert isinstance(reorganize.ORDER, int)


def test_a_dry_run_moves_nothing(ctx, drive):
    messages = " ".join(event.message for event in _run(ctx))
    assert "confirm" in messages.lower()
    assert _parent_names(drive, "d1", "d2", "d3") == {"2023-12", "back_2024_01"}


def test_a_dry_run_names_the_moves(ctx):
    messages = [event.message for event in _run(ctx)]
    assert any("back_2024_01/IMG_2.HEIC" in m and "2024-01" in m for m in messages)


def test_confirm_moves_every_file_into_its_bucket(ctx, drive):
    _run(ctx, confirm=True)
    assert _parent_names(drive, "d1", "d2", "d3") == {"2024-01"}


def test_confirm_updates_the_local_index(ctx):
    _run(ctx, confirm=True)
    paths = {
        row["drive_id"]: row["parent_path"]
        for row in ctx.conn.execute("SELECT drive_id, parent_path FROM drive_files")
    }
    assert paths == {"d1": "2024-01", "d2": "2024-01", "d3": "2024-01"}


def test_confirm_updates_the_catalogued_plan(ctx):
    _run(ctx, confirm=True)
    row = ctx.conn.execute(
        "SELECT target_folder FROM media WHERE drive_file_id = 'd1'"
    ).fetchone()
    assert row["target_folder"] == "2024-01"


def test_name_collisions_are_renamed(ctx, drive):
    _run(ctx, confirm=True)
    names = {drive.get_file(fid).name for fid in ("d1", "d3")}
    assert names == {"IMG_1.HEIC", "IMG_1~cccccc.HEIC"}


def test_emptied_and_already_empty_folders_are_trashed(ctx, drive):
    _run(ctx, confirm=True)
    remaining = {f.name for f in drive.list_children("photos", folders_only=True)}
    assert remaining == {"2024-01"}


def test_the_place_property_is_stripped_from_moved_files(ctx, drive):
    drive.update_properties("d2", {"place": "Warsaw"})
    _run(ctx, confirm=True)
    assert "place" not in drive.app_properties("d2")


def test_the_place_property_is_stripped_from_unmoved_catalogued_files(ctx, drive):
    # Refile d1 so it is already where it belongs, then confirm.
    ctx.conn.execute(
        "UPDATE drive_files SET parent_path = '2024-01' WHERE drive_id = 'd1'"
    )
    ctx.conn.execute(
        "UPDATE media SET target_folder = '2024-01' WHERE drive_file_id = 'd1'"
    )
    ctx.conn.commit()
    drive.add_folder("f-new", "2024-01", parent="photos")
    drive.move("d1", add_parent="f-new", remove_parent="f-old")
    drive.update_properties("d1", {"place": "Warsaw"})

    _run(ctx, confirm=True)

    assert "place" not in drive.app_properties("d1")


def test_undated_files_go_to_the_unknown_folder(ctx, drive):
    drive.add_file("d9", "IMG_9.MOV", b"m", parent="f-back")
    ctx.conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
        " mime_type) VALUES ('d9', 'IMG_9.MOV', 'back_2024_01', 'dd', 1,"
        " 'video/quicktime')"
    )
    ctx.conn.commit()
    _run(ctx, confirm=True)
    assert _parent_names(drive, "d9") == {"unknown-date"}


def test_a_missing_writer_is_reported_not_crashed(ctx):
    ctx.writer = None
    events = _run(ctx)
    assert events[-1].level == "error"


def test_an_empty_index_asks_for_a_scan(ctx):
    ctx.conn.execute("DELETE FROM drive_files")
    ctx.conn.execute("DELETE FROM media")
    ctx.conn.commit()
    events = _run(ctx)
    assert events[-1].level == "error"
    assert "Scan" in events[-1].message

import pytest

from photolib.actions import sync_tags
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.settings_repo import SettingsRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"a", parent="root")
    fake.add_file("d2", "IMG_2.HEIC", b"b", parent="root")
    return fake


@pytest.fixture
def ctx(conn, drive, tmp_path):
    for drive_id in ("d1", "d2"):
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
            "VALUES (?, 'IMG.HEIC', '2025-05', 'md5', 1, 'image/heic')",
            (drive_id,),
        )
    conn.commit()
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )
    return ActionContext(
        conn=conn, drive=drive, settings=SettingsRepo(conn), config=config,
        writer=drive,
    )


def _tag(conn, name: str, slug: str, drive_ids: list[str]) -> None:
    cursor = conn.execute(
        "INSERT INTO tags (name, slug, color) VALUES (?, ?, '#f00')", (name, slug)
    )
    for drive_id in drive_ids:
        conn.execute(
            "INSERT INTO file_tags (drive_id, tag_id) VALUES (?, ?)",
            (drive_id, cursor.lastrowid),
        )
    conn.commit()


def _run(ctx, **params) -> list:
    return list(sync_tags.run(ctx, sync_tags.Params(**params)))


def test_declares_itself_to_the_registry():
    assert sync_tags.ID == "sync_tags"
    assert isinstance(sync_tags.ORDER, int)


def test_a_dry_run_changes_nothing(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    messages = " ".join(event.message for event in _run(ctx))

    assert drive.app_properties("d1") == {}
    assert "confirm" in messages.lower()


def test_a_dry_run_names_what_it_would_add(ctx):
    _tag(ctx.conn, "Family", "family", ["d1"])
    messages = [event.message for event in _run(ctx)]
    assert any("t_family" in message and "d1" in message for message in messages)


def test_confirm_writes_the_property(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    assert drive.app_properties("d1") == {"t_family": "1"}


def test_confirm_records_what_it_wrote(ctx):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    row = ctx.conn.execute(
        "SELECT synced_tags FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert row["synced_tags"] == "family"


def test_untagging_removes_the_property_on_the_next_sync(ctx, drive):
    """The drift this design exists to prevent."""
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    ctx.conn.execute("DELETE FROM file_tags")
    ctx.conn.commit()
    _run(ctx, confirm=True)

    assert drive.app_properties("d1") == {}


def test_a_file_that_was_never_tagged_is_not_visited(ctx, drive):
    """Visiting all 1,284 files would cost 1,284 API calls for nothing."""
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)
    assert drive.app_properties("d2") == {}


def test_properties_organize_wrote_are_left_alone(ctx, drive):
    """Only t_* belongs to sync_tags. capture_time and place are not its business."""
    drive.update_properties("d1", {"place": "Warsaw", "source_crc": "abc"})
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    assert drive.app_properties("d1") == {
        "place": "Warsaw", "source_crc": "abc", "t_family": "1"
    }


def test_a_file_already_in_sync_is_not_written_again(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    _run(ctx, confirm=True)

    messages = " ".join(event.message for event in _run(ctx, confirm=True))

    assert "0 file(s) to change" in messages
    assert drive.app_properties("d1") == {"t_family": "1"}


def test_trashed_files_are_skipped(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1"])
    ctx.conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    ctx.conn.commit()
    _run(ctx, confirm=True)
    assert drive.app_properties("d1") == {}


def test_too_many_tags_is_refused_not_attempted(ctx, drive):
    """Drive caps appProperties at 30; Organize already used about five."""
    for index in range(26):
        _tag(ctx.conn, f"Tag {index}", f"tag-{index}", ["d1"])

    events = _run(ctx, confirm=True)

    assert any(event.level == "warn" for event in events)
    assert drive.app_properties("d1") == {}


def test_limit_caps_the_batch(ctx):
    _tag(ctx.conn, "Family", "family", ["d1", "d2"])
    messages = " ".join(event.message for event in _run(ctx, limit=1))
    assert "1 file(s)" in messages


def test_a_missing_writer_is_reported_not_crashed(ctx):
    ctx.writer = None
    events = _run(ctx)
    assert events[-1].level == "error"


def test_a_drive_failure_on_one_file_does_not_stop_the_run(ctx, drive):
    _tag(ctx.conn, "Family", "family", ["d1", "d2"])
    drive.trash("d1")          # d1 vanishes; its update will raise NotFoundError

    events = _run(ctx, confirm=True)

    assert any(event.level == "error" for event in events)
    assert drive.app_properties("d2") == {"t_family": "1"}

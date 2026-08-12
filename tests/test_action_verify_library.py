import hashlib

import pytest

from photolib.actions import verify_library
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive

CONTENT = b"same-bytes-one" * 10


def _messages(ctx):
    return [e.message for e in verify_library.run(ctx, verify_library.Params())]


def _warn_messages(ctx):
    """Just the drift-category reports — one per category that fired.

    Filters out the informational "Drive holds N file(s)" and closing
    "No drift"/"Nothing was changed" events, so a count of these is a count
    of categories, letting a test assert a single mutation trips exactly one
    category rather than bleeding into others.
    """
    return [
        e.message for e in verify_library.run(ctx, verify_library.Params())
        if e.level == "warn"
    ]


def _config(tmp_path) -> Config:
    return Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )


def _seed_one_verified_upload(conn, drive_id: str, folder: str) -> str:
    """Archive/entry/media rows for one file the pipeline uploaded and Drive
    confirmed, plus the matching `drive_files` index row. Returns its MD5.
    """
    md5 = hashlib.md5(CONTENT).hexdigest()
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, "
        "compressed_size, method, local_header_offset, kind) "
        "VALUES (1, 'Takeout/IMG_1.HEIC', 'IMG_1.HEIC', 111, ?, ?, 0, 0, 'media')",
        (len(CONTENT), len(CONTENT)),
    )
    conn.commit()
    repo = MediaRepo(conn)
    repo.upsert_media(1, target_folder=folder, target_name="IMG_1.HEIC")
    repo.mark_uploaded(1, drive_file_id=drive_id, md5=md5)
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
        "VALUES (?, 'IMG_1.HEIC', ?, ?, ?, 'image/heic')",
        (drive_id, folder, md5, len(CONTENT)),
    )
    conn.commit()
    return md5


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("photos", "Photos")
    fake.add_folder("f-2023", "2023-11", parent="photos")
    fake.add_file("up1", "IMG_1.HEIC", CONTENT, parent="f-2023")
    return fake


@pytest.fixture
def verify_context(conn, drive, tmp_path):
    """One verified upload, consistent with Drive: the baseline every
    individual test then disturbs in exactly one way."""
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    _seed_one_verified_upload(conn, "up1", "2023-11")
    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=_config(tmp_path),
        writer=drive,
    )


@pytest.fixture
def clean_verify_context(conn, tmp_path):
    """A separate, independently-built library with nothing disturbed."""
    fresh_drive = FakeDrive()
    fresh_drive.add_folder("photos", "Photos")
    fresh_drive.add_folder("f-2023", "2023-11", parent="photos")
    fresh_drive.add_file("up1", "IMG_1.HEIC", CONTENT, parent="f-2023")

    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    _seed_one_verified_upload(conn, "up1", "2023-11")

    return ActionContext(
        conn=conn, drive=fresh_drive, settings=settings, config=_config(tmp_path),
        writer=fresh_drive,
    )


def test_reports_a_file_deleted_outside_the_app(verify_context, conn):
    conn.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'gone', "
        "md5 = 'x' WHERE id = 1"
    )
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("no longer in Drive" in m for m in warnings)
    assert len(warnings) == 1  # this mutation alone, not any other category


def test_reports_a_file_moved_outside_the_app(verify_context, conn):
    # The action's "moved" check compares Drive's live parent path against
    # `media.target_folder` — it never consults `drive_files` for this (that
    # table is only used for the orphan-tag check). So the drift has to be
    # introduced on the catalog's own idea of where the file belongs, not on
    # the `drive_files` index row.
    conn.execute("UPDATE media SET target_folder = 'elsewhere' WHERE id = 1")
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("moved" in m for m in warnings)
    assert len(warnings) == 1


def test_reports_an_md5_mismatch(verify_context, conn):
    conn.execute("UPDATE media SET md5 = 'not-what-drive-says' WHERE id = 1")
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("MD5" in m for m in warnings)
    assert len(warnings) == 1


def test_reports_a_done_row_never_confirmed(verify_context, conn):
    conn.execute(
        "UPDATE media SET upload_status = 'done', md5 = NULL WHERE id = 1"
    )
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("never confirmed" in m for m in warnings)
    assert len(warnings) == 1


def test_reports_orphan_tags(verify_context, conn):
    conn.execute("INSERT INTO tags (name, slug) VALUES ('x', 'x')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('ghost', 1)")
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("no longer exist" in m for m in warnings)
    assert len(warnings) == 1


def test_a_missing_file_with_no_md5_is_reported_in_both_categories(
    verify_context, conn
):
    """Finding A: `missing` and `unconfirmed` are not mutually exclusive.

    A row with a real `drive_file_id` that Drive no longer holds, and whose
    `md5` was never confirmed, is a file that is both physically gone *and*
    was never verified — two different problems calling for two different
    fixes (re-upload vs. backfill an md5). Collapsing them into only the
    milder "unconfirmed" category would hide the gone-ness from the
    operator, defeating the point of an action whose only job is accurate
    categorisation.
    """
    conn.execute(
        "UPDATE media SET drive_file_id = 'gone', md5 = NULL WHERE id = 1"
    )
    conn.commit()
    warnings = _warn_messages(verify_context)
    assert any("no longer in Drive" in m for m in warnings)
    assert any("never confirmed" in m for m in warnings)
    assert len(warnings) == 2  # exactly these two categories, nothing else


def test_report_shows_every_example_up_to_the_cap():
    rows = [f"file-{i}.heic" for i in range(20)]
    event = verify_library._report("thing(s)", rows, 0.5)
    assert "more" not in event.message
    assert all(row in event.message for row in rows)


def test_report_truncates_and_counts_the_overflow():
    rows = [f"file-{i}.heic" for i in range(21)]
    event = verify_library._report("thing(s)", rows, 0.5)
    assert "(+1 more)" in event.message
    assert rows[-1] not in event.message  # the 21st row was not listed
    assert all(row in event.message for row in rows[:20])


def test_writes_nothing(verify_context, conn, drive):
    before = conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0]
    _messages(verify_context)
    assert conn.execute(
        "SELECT COUNT(*) FROM drive_files"
    ).fetchone()[0] == before
    assert drive.trashed == []


def test_a_clean_library_reports_no_drift(clean_verify_context):
    assert any("No drift" in m for m in _messages(clean_verify_context))

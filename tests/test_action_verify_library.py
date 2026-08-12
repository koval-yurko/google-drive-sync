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
    assert any("no longer in Drive" in m for m in _messages(verify_context))


def test_reports_a_file_moved_outside_the_app(verify_context, conn):
    # The action's "moved" check compares Drive's live parent path against
    # `media.target_folder` — it never consults `drive_files` for this (that
    # table is only used for the orphan-tag check). So the drift has to be
    # introduced on the catalog's own idea of where the file belongs, not on
    # the `drive_files` index row.
    conn.execute("UPDATE media SET target_folder = 'elsewhere' WHERE id = 1")
    conn.commit()
    assert any("moved" in m for m in _messages(verify_context))


def test_reports_an_md5_mismatch(verify_context, conn):
    conn.execute("UPDATE media SET md5 = 'not-what-drive-says'")
    conn.commit()
    assert any("MD5" in m for m in _messages(verify_context))


def test_reports_a_done_row_never_confirmed(verify_context, conn):
    conn.execute("UPDATE media SET upload_status = 'done', md5 = NULL")
    conn.commit()
    assert any("never confirmed" in m for m in _messages(verify_context))


def test_reports_orphan_tags(verify_context, conn):
    conn.execute("INSERT INTO tags (name, slug) VALUES ('x', 'x')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('ghost', 1)")
    conn.commit()
    assert any("no longer exist" in m for m in _messages(verify_context))


def test_writes_nothing(verify_context, conn, drive):
    before = conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0]
    _messages(verify_context)
    assert conn.execute(
        "SELECT COUNT(*) FROM drive_files"
    ).fetchone()[0] == before
    assert drive.trashed == []


def test_a_clean_library_reports_no_drift(clean_verify_context):
    assert any("No drift" in m for m in _messages(clean_verify_context))

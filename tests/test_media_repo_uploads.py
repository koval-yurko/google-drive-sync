import pytest

from photolib.db.media_repo import MediaRepo


@pytest.fixture
def repo(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 99)"
    )
    conn.executemany(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind)"
        " VALUES (1,?,?,?,?,?,8,?, 'media')",
        [
            ("d/IMG_1.HEIC", "IMG_1.HEIC", 111, 100, 60, 0),
            ("d/IMG_2.MOV", "IMG_2.MOV", 222, 200, 120, 500),
        ],
    )
    conn.commit()
    r = MediaRepo(conn)
    r.upsert_media(1)
    r.upsert_media(2)
    r.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    r.set_plan(2, target_folder="2019-01", target_name="IMG_2.MOV")
    return r


def test_pending_uploads_carries_everything_a_transfer_needs(repo):
    row = repo.pending_uploads()[0]
    for field in ("entry_id", "target_folder", "target_name", "path", "name",
                  "crc32", "size", "compressed_size", "method",
                  "local_header_offset", "archive_drive_id"):
        assert field in row.keys(), field
    assert row["local_header_offset"] == 0


def test_pending_uploads_is_ordered_for_locality(repo):
    """Within an archive, read in offset order so range reads stay local."""
    offsets = [r["local_header_offset"] for r in repo.pending_uploads()]
    assert offsets == sorted(offsets)


def test_marking_uploaded_removes_it_from_pending(repo):
    repo.mark_uploaded(1, drive_file_id="f1", md5="abc")
    assert [r["entry_id"] for r in repo.pending_uploads()] == [2]
    assert repo.summary()["uploaded"] == 1


def test_a_successful_upload_clears_its_session(repo):
    repo.save_session(1, "https://upload/s1")
    repo.mark_uploaded(1, drive_file_id="f1", md5="abc")
    row = repo.all_media()[0]
    assert row["upload_session_uri"] is None
    assert row["error"] is None


def test_failures_are_excluded_unless_asked_for(repo):
    repo.mark_failed(1, "CRC mismatch")
    assert [r["entry_id"] for r in repo.pending_uploads()] == [2]
    assert {r["entry_id"] for r in repo.pending_uploads(retry_errors=True)} == {1, 2}
    assert repo.summary()["errors"] == 1


def test_a_failure_keeps_its_session_so_it_can_resume(repo):
    repo.save_session(1, "https://upload/s1")
    repo.mark_failed(1, "network died", offset=4096)
    row = repo.all_media()[0]
    assert row["upload_session_uri"] == "https://upload/s1"
    assert row["upload_offset"] == 4096
    assert row["error"] == "network died"


def test_attempts_count_up(repo):
    repo.mark_failed(1, "once")
    repo.mark_failed(1, "twice")
    assert repo.all_media()[0]["attempts"] == 2


def test_reset_upload_clears_everything_about_the_attempt(repo):
    repo.save_session(1, "https://upload/s1")
    repo.mark_failed(1, "bad", offset=10)
    repo.reset_upload(1)
    row = repo.all_media()[0]
    assert row["upload_status"] == "pending"
    assert (row["error"], row["upload_session_uri"], row["upload_offset"]) == (
        None, None, 0
    )


def test_limit_caps_the_batch(repo):
    assert len(repo.pending_uploads(limit=1)) == 1

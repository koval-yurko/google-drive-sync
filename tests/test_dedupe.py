import hashlib

from photolib.dedupe import Removal, apply_removal, plan_removals
from tests.fakes.fake_drive import FakeDrive


class _IdempotentTrashWriter:
    """Models Drive's real trash semantics for a replay test.

    The shared `FakeDrive.trash()` deletes its record outright — convenient
    for tests that want a file to vanish entirely (see
    test_action_sync_tags.py), but stricter than real Drive: a trashed file
    stays retrievable, and therefore re-trashable, for 30 days
    (https://developers.google.com/workspace/drive/api/guides/delete). This
    stub reflects that: `trash()` never raises, however many times it is
    called on the same id.
    """

    def __init__(self) -> None:
        self.trashed: list[str] = []

    def trash(self, file_id: str) -> None:
        self.trashed.append(file_id)


def _library() -> FakeDrive:
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("m1", "2025-01", parent="root")
    drive.add_file("a", "one.heic", b"same", parent="m1")
    drive.add_file("b", "one-copy.heic", b"same", parent="m1")
    drive.add_file("c", "other.heic", b"different", parent="m1")
    return drive


def test_one_copy_of_each_group_survives(conn):
    # Neither copy is a verified upload, so the tiebreak is the plain path
    # string: "one-copy.heic" sorts before "one.heic" ('-' < '.' in ASCII),
    # so "b" is kept and "a" is the redundant copy.
    removals, _, total = plan_removals(_library(), conn, "root")
    assert [r.drive_id for r in removals] == ["a"]
    assert removals[0].keeper_id == "b"
    assert total == 3


def _verified_upload(conn, drive_id: str) -> None:
    """Record `drive_id` as a copy this pipeline uploaded and verified."""
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z', 'z.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries "
        "(archive_id, path, name, crc32, size, compressed_size, method, "
        " local_header_offset, kind) "
        "VALUES (1, 'Takeout/one.heic', 'one.heic', 111, 4, 4, 8, 0, 'media')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, upload_status, drive_file_id, md5) "
        "VALUES (1, 'done', ?, 'x')",
        (drive_id,),
    )
    conn.commit()


def test_a_verified_upload_is_preferred_as_the_keeper(conn):
    _verified_upload(conn, "b")
    removals, _, _total = plan_removals(_library(), conn, "root")
    assert [r.drive_id for r in removals] == ["a"]
    assert removals[0].keeper_id == "b"


def test_zero_byte_files_are_reported_not_removed(conn):
    drive = _library()
    drive.add_file("z1", "empty1.heic", b"", parent="m1")
    drive.add_file("z2", "empty2.heic", b"", parent="m1")
    removals, zero, total = plan_removals(drive, conn, "root")
    assert set(zero) == {"z1", "z2"}
    assert "z1" not in {r.drive_id for r in removals}
    assert total == 5


def test_removal_carries_name_and_parent_path(conn):
    removals, _zero, _total = plan_removals(_library(), conn, "root")
    assert removals[0].name == "one.heic"
    assert removals[0].parent_path == "2025-01"
    assert removals[0].md5 == hashlib.md5(b"same").hexdigest()
    assert removals[0].keeper_path == "2025-01/one-copy.heic"
    assert removals[0].size == len(b"same")


def test_apply_removal_is_safe_to_replay(conn):
    """A resumed run can call apply_removal a second time for an item whose
    effect already landed but whose `job_items` row never reached `done`
    (see photolib/db/job_items_repo.py). Applying it twice must not raise
    and must leave the catalog in the same state as applying it once."""
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type) "
        "VALUES ('a', 'one.heic', '2025-01', 'same', 4, 'image/heic')"
    )
    conn.commit()
    removal = Removal(
        drive_id="a", name="one.heic", parent_path="2025-01", md5="same",
        keeper_id="b", keeper_path="2025-01/one-copy.heic", size=4,
    )
    writer = _IdempotentTrashWriter()

    apply_removal(writer, removal, conn)
    apply_removal(writer, removal, conn)  # replay

    assert writer.trashed == ["a", "a"]
    row = conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = 'a'"
    ).fetchone()
    assert row["trashed_at"] is not None


def test_apply_removal_stamps_the_catalog_with_the_trash_time(conn):
    """Trashing must record `trashed_at`, so the Library stops showing the
    copy immediately rather than waiting for the next Scan."""
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type) "
        "VALUES ('a', 'one.heic', '2025-01', 'same', 4, 'image/heic')"
    )
    conn.commit()
    removals, _zero, _total = plan_removals(_library(), conn, "root")
    assert removals, "fixture must produce at least one removal"

    apply_removal(_IdempotentTrashWriter(), removals[0], conn)

    row = conn.execute(
        "SELECT trashed_at FROM drive_files WHERE drive_id = ?",
        (removals[0].drive_id,),
    ).fetchone()
    assert row["trashed_at"] is not None

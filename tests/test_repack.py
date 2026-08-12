from datetime import datetime, timezone

from photolib.drive.client import DriveFile
from photolib.drive.errors import DriveError
from photolib.repack import Move, apply_move, apply_sweep, plan_moves, plan_sweep
from tests.fakes.fake_drive import FakeDrive


class _IdempotentTrashWriter:
    """Models Drive's real trash semantics for a replay test.

    The shared `FakeDrive.trash()` deletes its record outright, which is
    stricter than real Drive: a trashed item stays retrievable, and
    therefore re-trashable, for 30 days
    (https://developers.google.com/workspace/drive/api/guides/delete). This
    stub reflects that: `trash()` never raises, however many times it is
    called on the same id.
    """

    def __init__(self) -> None:
        self.trashed: list[str] = []

    def trash(self, file_id: str) -> None:
        self.trashed.append(file_id)


class _StrictMoveWriter:
    """Models a Drive that *does* reject a stale `removeParents` — the one
    behaviour the v3 `files.update` reference leaves undocumented (see
    `repack.apply_move`). Exercises apply_move's recovery path without
    betting on which way real Drive actually behaves.
    """

    def __init__(self, parents: dict[str, list[str]]) -> None:
        self.parents = parents
        self.calls = 0

    def move(self, file_id, *, add_parent, remove_parent, name=None,
             properties=None) -> None:
        self.calls += 1
        current = self.parents[file_id]
        if remove_parent not in current:
            raise DriveError("400: parent is not a parent of this file")
        updated = [p for p in current if p != remove_parent]
        if add_parent not in updated:
            updated.append(add_parent)
        self.parents[file_id] = updated


class _ParentsDrive:
    """Just enough of DriveClient for apply_move's post-failure check."""

    def __init__(self, parents: dict[str, list[str]]) -> None:
        self._parents = parents

    def get_file(self, file_id: str) -> DriveFile:
        return DriveFile(
            id=file_id, name="x", mimeType="application/octet-stream",
            parents=self._parents[file_id],
        )


def _epoch(month: str) -> int:
    return int(
        datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc).timestamp()
    )


def _seed(conn, files: list[tuple[str, str, str]]) -> None:
    """files: (drive_id, parent_path, capture month like '2025-01')."""
    conn.executemany(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, capture_hint) "
        "VALUES (?, ?, ?, ?, 4, 'image/heic', ?)",
        [
            (drive_id, f"{drive_id}.heic", parent, drive_id, _epoch(month))
            for drive_id, parent, month in files
        ],
    )
    conn.commit()


def test_a_file_already_in_its_bucket_produces_no_move(conn):
    _seed(conn, [("d1", "2025-01", "2025-01")])
    assert plan_moves(FakeDrive(), conn, "root") == []


def test_a_file_in_the_wrong_folder_is_moved(conn):
    _seed(conn, [("d1", "back_2019", "2025-01")])
    moves = plan_moves(FakeDrive(), conn, "root")
    assert [(m.drive_id, m.from_path, m.to_folder) for m in moves] == [
        ("d1", "back_2019", "2025-01")
    ]


def test_excluded_files_do_not_reserve_bucket_space(conn):
    """A file about to be trashed must not push a month into its own bucket.

    150 files in 2025-01 alone already exceed MAX_BUCKET (130), so on its
    own that month always stands as its own bucket, whether or not a small
    neighbouring month (2025-02) exists. Once 100 of those 150 are excluded
    (dedupe is about to trash them), the remaining 50 are small enough to
    merge with that neighbour into one combined bucket — which also drags
    the neighbour's already-correctly-filed file into needing a move.
    """
    _seed(
        conn,
        [(f"d{i}", "back_2019", "2025-01") for i in range(150)]
        + [("n1", "2025-02", "2025-02")],
    )
    with_all = {m.to_folder for m in plan_moves(FakeDrive(), conn, "root")}
    doomed = {f"d{i}" for i in range(100)}
    without = {
        m.to_folder
        for m in plan_moves(FakeDrive(), conn, "root", exclude=doomed)
    }
    assert with_all == {"2025-01"}
    assert without != with_all


def test_a_name_collision_in_the_destination_is_renamed(conn):
    conn.executemany(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, capture_hint) "
        "VALUES (?, 'IMG_1.heic', ?, ?, 4, 'image/heic', ?)",
        [
            ("d1", "back_2019", "aaa", _epoch("2025-01")),
            ("d2", "back_2020", "bbb", _epoch("2025-01")),
        ],
    )
    conn.commit()
    names = {m.new_name for m in plan_moves(FakeDrive(), conn, "root")}
    assert len(names) == 2, "two files cannot land on one name"


def test_sweep_lists_only_empty_folders(conn):
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("empty", "back_2019", parent="root")
    drive.add_folder("full", "2025-01", parent="root")
    drive.add_file("f", "a.heic", b"a", parent="full")
    assert [name for _, name in plan_sweep(drive, "root")] == ["back_2019"]


def test_apply_sweep_is_safe_to_replay(conn):
    """A resumed run can call apply_sweep a second time for a folder whose
    trash already landed but whose `job_items` row never reached `done`.
    Applying it twice must not raise."""
    writer = _IdempotentTrashWriter()

    apply_sweep(writer, "empty-folder")
    apply_sweep(writer, "empty-folder")  # replay

    assert writer.trashed == ["empty-folder", "empty-folder"]


def test_apply_move_is_safe_to_replay_when_drive_rejects_a_stale_removeparents(conn):
    """The narrow case `apply_move`'s guard exists for: Drive rejects the
    second call's `removeParents` because the file no longer has that
    parent. The move must still complete harmlessly and the file must not
    end up re-parented into anything odd."""
    _seed(conn, [("d1", "back_2019", "2025-01")])
    move = Move(
        drive_id="d1", name="d1.heic", new_name="d1.heic",
        from_path="back_2019", to_folder="2025-01",
    )
    folder_ids = {"back_2019": "old-id", "2025-01": "new-id"}
    parents = {"d1": ["old-id"]}
    writer = _StrictMoveWriter(parents)
    drive = _ParentsDrive(parents)

    apply_move(writer, conn, move, folder_ids, drive=drive)
    assert writer.calls == 1
    assert parents["d1"] == ["new-id"]

    apply_move(writer, conn, move, folder_ids, drive=drive)  # replay
    assert writer.calls == 2
    assert parents["d1"] == ["new-id"], "must not be corrupted by the replay"

    row = conn.execute(
        "SELECT parent_path, name FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert (row["parent_path"], row["name"]) == ("2025-01", "d1.heic")


def test_apply_move_reraises_a_genuine_drive_failure(conn):
    """The recovery path must not swallow a real failure — only one where
    the file has already reached the intended target."""
    _seed(conn, [("d1", "back_2019", "2025-01")])
    move = Move(
        drive_id="d1", name="d1.heic", new_name="d1.heic",
        from_path="back_2019", to_folder="2025-01",
    )
    folder_ids = {"back_2019": "old-id", "2025-01": "new-id"}
    # The file never actually moved — still sitting on some other parent.
    parents = {"d1": ["unrelated-id"]}
    writer = _StrictMoveWriter(parents)
    drive = _ParentsDrive(parents)

    try:
        apply_move(writer, conn, move, folder_ids, drive=drive)
        assert False, "expected a DriveError"
    except DriveError:
        pass

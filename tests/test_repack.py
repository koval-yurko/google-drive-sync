from datetime import datetime, timezone

from photolib.repack import plan_moves, plan_sweep
from tests.fakes.fake_drive import FakeDrive


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

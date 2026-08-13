from datetime import datetime, timezone

from photolib.planning.layout import plan_moves, plan_sweep
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


def test_an_undated_file_goes_to_the_unknown_folder(conn):
    """A file Drive never dated has no month to bucket by, so it lands in
    `buckets.UNKNOWN_FOLDER` rather than being left where it is
    (layout.py: `fmap[month] if month else buckets.UNKNOWN_FOLDER`)."""
    conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type, capture_hint) "
        "VALUES ('d9', 'IMG_9.MOV', 'back_2019', 'ddd', 4, "
        "'video/quicktime', NULL)"
    )
    conn.commit()
    moves = plan_moves(FakeDrive(), conn, "root")
    assert [(m.drive_id, m.to_folder) for m in moves] == [("d9", "unknown-date")]


def test_a_catalogued_undated_file_ignores_its_drive_capture_hint(conn):
    """`LayoutRepo.live_files_for_layout` prefers `media.capture_time` once a
    file is catalogued.

    The `drive_files` hint is an upload timestamp, not a capture date, so it
    must not steer a file the catalog positively knows is undated — the
    `CASE WHEN m.id IS NULL` half of the query.
    """
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind) VALUES"
        " (1, 'p/d4.heic', 'd4.heic', 4, 1, 1, 8, 0, 'media')"
    )
    conn.execute(
        "INSERT INTO media (entry_id, capture_time, target_folder, target_name,"
        " upload_status, drive_file_id)"
        " VALUES (1, NULL, '2023-12', 'd4.heic', 'done', 'd4')"
    )
    # The stale hint says 2025-01; the catalog says undated and wins.
    _seed(conn, [("d4", "2023-12", "2025-01")])

    moves = plan_moves(FakeDrive(), conn, "root")
    assert [(m.drive_id, m.to_folder) for m in moves] == [("d4", "unknown-date")]


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

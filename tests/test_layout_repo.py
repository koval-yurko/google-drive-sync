import sqlite3

import pytest

from collections import Counter

from photolib.db.layout_repo import LayoutRepo


def _seed(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 1)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size,"
        " compressed_size, method, local_header_offset, kind) VALUES"
        " (1, 'p/IMG_1.HEIC', 'IMG_1.HEIC', 1, 1, 1, 8, 0, 'media')"
    )
    # A catalogued file: its month comes from media.capture_time.
    conn.execute(
        "INSERT INTO media (entry_id, capture_time, drive_file_id)"
        " VALUES (1, 1704067200, 'd1')"          # 2024-01
    )
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d1', 'IMG_1.HEIC', '2024-01', 1500000000)"
    )
    # An unaccounted legacy file: only its hint dates it.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d2', 'IMG_2.HEIC', 'back_2024_01', 1704153600)"   # 2024-01
    )
    # A trashed file counts for nothing.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint,"
        " trashed_at) VALUES ('d3', 'IMG_3.HEIC', 'x', 1704240000, 'now')"
    )
    # An undated legacy file is skipped by the histograms.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path)"
        " VALUES ('d4', 'IMG_4.HEIC', 'back_2024_01')"
    )
    conn.commit()


def test_unaccounted_months_skips_catalogued_trashed_and_undated(conn):
    _seed(conn)
    assert LayoutRepo(conn).unaccounted_months() == Counter({"2024-01": 1})


def test_capture_histogram_counts_media_and_unaccounted_files(conn):
    _seed(conn)
    assert LayoutRepo(conn).capture_histogram() == Counter({"2024-01": 2})


def test_capture_histogram_drops_excluded_ids_from_both_halves(conn):
    _seed(conn)
    repo = LayoutRepo(conn)
    assert repo.capture_histogram(exclude={"d1"}) == Counter({"2024-01": 1})
    assert repo.capture_histogram(exclude={"d1", "d2"}) == Counter()


def test_month_computed_in_sql_matches_utc(conn):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, capture_hint)"
        " VALUES ('d9', 'x.HEIC', 'p', 1700000000)"      # 2023-11 in UTC
    )
    conn.commit()
    assert LayoutRepo(conn).unaccounted_months() == Counter({"2023-11": 1})


def test_live_files_for_layout_returns_capture_and_skips_excluded(conn):
    _seed(conn)
    rows = LayoutRepo(conn).live_files_for_layout()
    by_id = {row["drive_id"]: row for row in rows}
    assert set(by_id) == {"d1", "d2", "d4"}
    # A catalogued file dates from media.capture_time, not the Drive hint.
    assert by_id["d1"]["capture"] == 1704067200
    assert by_id["d2"]["capture"] == 1704153600
    assert by_id["d1"]["parent_path"] == "2024-01"

    kept = LayoutRepo(conn).live_files_for_layout(exclude={"d2"})
    assert {row["drive_id"] for row in kept} == {"d1", "d4"}


def test_record_move_updates_both_tables_together(conn):
    _seed(conn)
    LayoutRepo(conn).record_move("d1", "2024-01 - 2024-03", "IMG_1~ab12.HEIC")

    drive_row = conn.execute(
        "SELECT parent_path, name FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert drive_row["parent_path"] == "2024-01 - 2024-03"
    assert drive_row["name"] == "IMG_1~ab12.HEIC"

    media_row = conn.execute(
        "SELECT target_folder, target_name FROM media WHERE drive_file_id = 'd1'"
    ).fetchone()
    assert media_row["target_folder"] == "2024-01 - 2024-03"
    assert media_row["target_name"] == "IMG_1~ab12.HEIC"


def test_record_move_rolls_back_when_the_second_update_fails(conn):
    """Both tables agree or neither changes. Today's two-statement version
    can leave drive_files moved and media not."""
    _seed(conn)
    repo = LayoutRepo(conn)

    real_execute = conn.execute
    calls = []

    def failing_execute(sql, *args, **kwargs):
        calls.append(sql)
        if sql.startswith("UPDATE media"):
            raise sqlite3.OperationalError("boom")
        return real_execute(sql, *args, **kwargs)

    conn.execute = failing_execute
    try:
        with pytest.raises(sqlite3.OperationalError):
            repo.record_move("d1", "moved", "moved.HEIC")
    finally:
        conn.execute = real_execute

    unchanged = conn.execute(
        "SELECT parent_path FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert unchanged["parent_path"] == "2024-01"

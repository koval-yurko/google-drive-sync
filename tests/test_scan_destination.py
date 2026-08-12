from photolib.scan import index_destination
from tests.fakes.fake_drive import FakeDrive


def _tree() -> FakeDrive:
    drive = FakeDrive()
    drive.add_folder("root", "Photos")
    drive.add_folder("m1", "2025-01", parent="root")
    drive.add_folder("nested", "back_2019", parent="root")
    drive.add_folder("deep", "inner", parent="nested")
    drive.add_file("f1", "a.heic", b"aaa", parent="m1", mime_type="image/heic")
    drive.add_file("f2", "b.mov", b"bbb", parent="deep", mime_type="video/quicktime")
    return drive


def test_walks_every_depth_and_records_paths(conn):
    drive = _tree()
    walked = index_destination(drive, conn, "root")
    assert len(walked) == 2
    assert {f.id for f in walked} == {"f1", "f2"}
    rows = {
        r["drive_id"]: r["parent_path"]
        for r in conn.execute("SELECT drive_id, parent_path FROM drive_files")
    }
    assert rows == {"f1": "2025-01", "f2": "back_2019/inner"}


def test_every_folder_is_listed_exactly_once(conn):
    drive = _tree()
    calls: list[str] = []
    original = drive.list_children
    drive.list_children = lambda fid: (calls.append(fid), original(fid))[1]

    index_destination(drive, conn, "root")
    assert sorted(calls) == ["deep", "m1", "nested", "root"]


def test_the_index_is_written_in_one_sweep(conn):
    """upsert_drive_files deletes rows not carrying its own timestamp, so a
    walk that called it per folder would keep only the last folder's files."""
    drive = _tree()
    index_destination(drive, conn, "root")
    stamps = {
        r["indexed_at"]
        for r in conn.execute("SELECT indexed_at FROM drive_files")
    }
    assert len(stamps) == 1


def test_a_file_gone_from_drive_is_dropped_on_the_next_walk(conn):
    """The sweep inside upsert_drive_files is how deletions reach the catalog."""
    index_destination(_tree(), conn, "root")

    smaller = FakeDrive()
    smaller.add_folder("root", "Photos")
    smaller.add_folder("m1", "2025-01", parent="root")
    smaller.add_file("f1", "a.heic", b"aaa", parent="m1", mime_type="image/heic")

    assert len(index_destination(smaller, conn, "root")) == 1
    remaining = [
        r["drive_id"] for r in conn.execute("SELECT drive_id FROM drive_files")
    ]
    assert remaining == ["f1"]

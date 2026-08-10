from photolib.db.scan_repo import ScanRepo
from photolib.ziparchive.reader import ZipEntry


def entry(path: str, name: str, offset: int = 0) -> ZipEntry:
    return ZipEntry(
        path=path, name=name, crc32=1, size=10, compressed_size=5,
        method=8, local_header_offset=offset,
    )


def test_upsert_archive_returns_a_stable_id(conn):
    repo = ScanRepo(conn)
    first = repo.upsert_archive("z1", "takeout-001.zip", 100, "2026-01-01T00:00:00Z")
    again = repo.upsert_archive("z1", "takeout-001.zip", 100, "2026-01-01T00:00:00Z")
    assert first == again


def test_archive_is_current_tracks_size_and_mtime(conn):
    repo = ScanRepo(conn)
    repo.upsert_archive("z1", "a.zip", 100, "2026-01-01T00:00:00Z")
    repo.mark_indexed(repo.upsert_archive("z1", "a.zip", 100, "2026-01-01T00:00:00Z"))
    assert repo.archive_is_current("z1", 100, "2026-01-01T00:00:00Z") is True
    assert repo.archive_is_current("z1", 999, "2026-01-01T00:00:00Z") is False
    assert repo.archive_is_current("z1", 100, "2026-06-01T00:00:00Z") is False
    assert repo.archive_is_current("unknown", 100, None) is False


def test_archive_is_not_current_until_indexed(conn):
    repo = ScanRepo(conn)
    repo.upsert_archive("z1", "a.zip", 100, "t")
    assert repo.archive_is_current("z1", 100, "t") is False


def test_replace_entries_is_idempotent(conn):
    repo = ScanRepo(conn)
    aid = repo.upsert_archive("z1", "a.zip", 100, "t")
    entries = [entry("d/one.HEIC", "one.HEIC"), entry("d/one.HEIC.json", "one.HEIC.json", 50)]
    kinds = {"d/one.HEIC": "media", "d/one.HEIC.json": "sidecar"}
    repo.replace_entries(aid, entries, kinds)
    repo.replace_entries(aid, entries, kinds)
    assert repo.counts()["entries"] == 2
    assert repo.counts()["media"] == 1
    assert repo.counts()["sidecars"] == 1


def test_entries_of_kind_carries_the_archive_drive_id(conn):
    repo = ScanRepo(conn)
    aid = repo.upsert_archive("z1", "a.zip", 100, "t")
    repo.replace_entries(aid, [entry("d/one.HEIC", "one.HEIC")], {"d/one.HEIC": "media"})
    (row,) = repo.entries_of_kind("media")
    assert row["name"] == "one.HEIC"
    assert row["archive_drive_id"] == "z1"
    assert row["archive_name"] == "a.zip"
    assert row["local_header_offset"] == 0


def test_replace_drive_files_round_trips(conn):
    repo = ScanRepo(conn)
    repo.replace_drive_files([
        {"drive_id": "f1", "name": "IMG_1.HEIC", "parent_path": "back_2024_01",
         "md5": "abc", "size": 10},
        {"drive_id": "f2", "name": "IMG_1.HEIC", "parent_path": "back_2025_01",
         "md5": "def", "size": 20},
    ])
    by_name = repo.drive_file_names()
    assert len(by_name["IMG_1.HEIC"]) == 2
    assert {r["parent_path"] for r in by_name["IMG_1.HEIC"]} == {"back_2024_01", "back_2025_01"}


def test_replace_drive_files_clears_the_previous_index(conn):
    repo = ScanRepo(conn)
    repo.replace_drive_files([
        {"drive_id": "f1", "name": "old.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    repo.replace_drive_files([
        {"drive_id": "f2", "name": "new.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    assert "old.HEIC" not in repo.drive_file_names()
    assert repo.counts()["drive_files"] == 1

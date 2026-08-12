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


def test_upsert_drive_files_round_trips(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files([
        {"drive_id": "f1", "name": "IMG_1.HEIC", "parent_path": "back_2024_01",
         "md5": "abc", "size": 10},
        {"drive_id": "f2", "name": "IMG_1.HEIC", "parent_path": "back_2025_01",
         "md5": "def", "size": 20},
    ])
    by_name = repo.drive_file_names()
    assert len(by_name["IMG_1.HEIC"]) == 2
    assert {r["parent_path"] for r in by_name["IMG_1.HEIC"]} == {"back_2024_01", "back_2025_01"}


def test_upsert_drive_files_clears_the_previous_index(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files([
        {"drive_id": "f1", "name": "old.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    repo.upsert_drive_files([
        {"drive_id": "f2", "name": "new.HEIC", "parent_path": "p", "md5": None, "size": 1}
    ])
    assert "old.HEIC" not in repo.drive_file_names()
    assert repo.counts()["drive_files"] == 1


def _rows(*specs):
    return [
        {
            "drive_id": drive_id, "name": name, "parent_path": parent,
            "md5": "abc", "size": 10, "mime_type": mime,
        }
        for drive_id, name, parent, mime in specs
    ]


def test_upsert_drive_files_inserts(conn):
    ScanRepo(conn).upsert_drive_files(
        _rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic"))
    )
    row = conn.execute("SELECT * FROM drive_files").fetchone()
    assert (row["drive_id"], row["parent_path"], row["mime_type"]) == (
        "d1", "2025-05", "image/heic"
    )
    assert row["indexed_at"] is not None


def test_upsert_drive_files_keeps_tags_across_a_rescan(conn):
    """The bug this guards: re-Scan silently deleting every tag."""
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d1', 1)")
    conn.commit()

    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert conn.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 1


def test_upsert_drive_files_updates_a_moved_file(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-04", "image/heic")))
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    rows = list(conn.execute("SELECT parent_path FROM drive_files"))
    assert len(rows) == 1
    assert rows[0]["parent_path"] == "2025-05"


def test_upsert_drive_files_drops_what_left_drive(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(
        _rows(
            ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
            ("d2", "IMG_2.HEIC", "2025-05", "image/heic"),
        )
    )
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert [r["drive_id"] for r in conn.execute("SELECT drive_id FROM drive_files")] == ["d1"]


def test_upsert_drive_files_with_nothing_clears_the_index(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    repo.upsert_drive_files([])

    assert conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0] == 0


def test_upsert_drive_files_handles_more_rows_than_sqlite_variables(conn):
    rows = _rows(*[(f"d{n}", f"IMG_{n}.HEIC", "2025-05", "image/heic") for n in range(1500)])
    ScanRepo(conn).upsert_drive_files(rows)
    assert conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0] == 1500


def test_upsert_drive_files_revives_a_trashed_row(conn):
    """A file restored from Drive's trash must reappear in the library."""
    repo = ScanRepo(conn)
    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))
    conn.execute("UPDATE drive_files SET trashed_at = 'then' WHERE drive_id = 'd1'")
    conn.commit()

    repo.upsert_drive_files(_rows(("d1", "IMG_1.HEIC", "2025-05", "image/heic")))

    assert conn.execute("SELECT trashed_at FROM drive_files").fetchone()["trashed_at"] is None


def test_set_enrichment_and_unenriched(conn):
    repo = ScanRepo(conn)
    repo.upsert_drive_files([
        {"drive_id": "d1", "name": "a.heic", "parent_path": "2025-01",
         "md5": "x", "size": 3, "mime_type": "image/heic", "capture_hint": None},
    ])
    assert [r["drive_id"] for r in repo.unenriched()] == ["d1"]
    repo.set_enrichment("d1", capture_hint=1700000000, latitude=37.9,
                        longitude=23.7, country="Greece",
                        metadata_source="exif")
    assert repo.unenriched() == []
    row = conn.execute(
        "SELECT * FROM drive_files WHERE drive_id = 'd1'"
    ).fetchone()
    assert (row["country"], row["metadata_source"]) == ("Greece", "exif")

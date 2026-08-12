import sqlite3

import pytest

from photolib.db import catalog


def test_phase_two_tables_exist(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"sidecars", "media", "drive_files", "geocache"} <= names


def test_schema_version_is_five(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert catalog.SCHEMA_VERSION == 6


def test_upgrade_from_v1_keeps_existing_rows(tmp_path):
    db = tmp_path / "t.db"
    conn = catalog.connect(db)
    conn.execute("INSERT INTO settings (key, value) VALUES ('photos_root', 'x')")
    conn.commit()
    conn.execute("PRAGMA user_version = 1")   # pretend this is an old catalog
    conn.commit()
    conn.close()

    upgraded = catalog.connect(db)
    assert upgraded.execute("SELECT value FROM settings").fetchone()["value"] == "x"
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 6


def test_media_requires_a_known_entry(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO media (entry_id) VALUES (9999)")
        conn.commit()


def test_upload_status_rejects_unknown_values(tmp_path):
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES (1,'a/b.HEIC','b.HEIC',1,2,3,8,0,'media')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO media (entry_id, upload_status) VALUES (1, 'nonsense')")
        conn.commit()

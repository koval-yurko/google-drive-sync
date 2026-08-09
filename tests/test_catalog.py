import sqlite3

from photolib.db import catalog


def test_connect_creates_all_tables(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"settings", "archives", "entries", "jobs", "job_events"} <= names


def test_connect_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    catalog.connect(db).close()
    conn = catalog.connect(db)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == catalog.SCHEMA_VERSION


def test_rows_are_mappings(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    conn.execute("INSERT INTO settings (key, value) VALUES ('a', 'b')")
    row = conn.execute("SELECT * FROM settings").fetchone()
    assert row["key"] == "a"
    assert row["value"] == "b"


def test_foreign_keys_are_enforced(tmp_path):
    conn = catalog.connect(tmp_path / "test.db")
    try:
        conn.execute(
            "INSERT INTO job_events (job_id, ts, level, message) "
            "VALUES ('nonexistent', 0, 'info', 'x')"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return
    raise AssertionError("foreign key constraint was not enforced")

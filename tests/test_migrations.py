"""A catalog created fresh and a catalog upgraded in place must end identical."""

import sqlite3

from photolib.db import catalog, migrations


def _schema(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Every (table, column) pair in the database."""
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        (table, row["name"])
        for table in tables
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def test_version_is_three(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    assert migrations.SCHEMA_VERSION == 3
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_media_has_the_upload_session_columns(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    assert {
        "upload_session_uri", "upload_offset", "session_started_at", "attempts"
    } <= columns


def test_upgrading_a_v2_catalog_matches_a_fresh_one(tmp_path):
    """The bug this guards: 'created new' and 'upgraded' drifting apart."""
    old = tmp_path / "old.db"
    conn = catalog.connect(old)
    conn.execute("INSERT INTO settings (key, value) VALUES ('photos_root', 'x')")
    conn.commit()
    # Strip the v3 columns and rewind the version: a genuine v2 catalog.
    for column in ("upload_session_uri", "upload_offset", "session_started_at",
                   "attempts"):
        conn.execute(f"ALTER TABLE media DROP COLUMN {column}")
    conn.execute("ALTER TABLE drive_files DROP COLUMN trashed_at")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    upgraded = catalog.connect(old)
    fresh = catalog.connect(tmp_path / "fresh.db")

    assert _schema(upgraded) == _schema(fresh)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 3
    assert upgraded.execute("SELECT value FROM settings").fetchone()["value"] == "x"


def test_migrating_twice_is_harmless(tmp_path):
    db = tmp_path / "t.db"
    catalog.connect(db).close()
    conn = catalog.connect(db)
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3

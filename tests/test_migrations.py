"""A catalog created fresh and a catalog upgraded in place must end identical."""

import sqlite3

from photolib.db import catalog, migrations

V3_COLUMNS = (
    ("media", "upload_session_uri"),
    ("media", "upload_offset"),
    ("media", "session_started_at"),
    ("media", "attempts"),
    ("drive_files", "trashed_at"),
)
V4_COLUMNS = (
    ("drive_files", "mime_type"),
    ("drive_files", "synced_tags"),
)
# Added by the bucket-repacking branch, which removed the Place facet.
V5_COLUMNS = (
    ("drive_files", "capture_hint"),
)
# Everything the two-flows branch added at schema version 6. Stripping these
# is what makes the upgrade path actually run its ALTER TABLE statements —
# without it, both sides of the parity comparison get the columns from
# schema.sql and the test passes without proving anything.
V6_COLUMNS = (
    ("jobs", "run_id"),
    ("jobs", "resumed_from"),
    ("jobs", "phase"),
    ("jobs", "items_done"),
    ("jobs", "items_total"),
    ("media", "plan_verdict"),
    ("media", "plan_match"),
    ("drive_files", "country"),
    ("drive_files", "latitude"),
    ("drive_files", "longitude"),
    ("drive_files", "metadata_source"),
)
# Every column the simulated old catalog must lose.
# `test_every_added_column_is_covered_by_the_strip_list` holds this to
# `migrations._ADDED_COLUMNS`, so a new column cannot silently skip the
# upgrade path again.
STRIPPED_COLUMNS = V3_COLUMNS + V4_COLUMNS + V5_COLUMNS + V6_COLUMNS


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


def test_version_is_five(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    assert migrations.SCHEMA_VERSION == 6
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_media_has_the_upload_session_columns(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    assert {
        "upload_session_uri", "upload_offset", "session_started_at", "attempts"
    } <= columns


def test_drive_files_carries_mime_type_and_sync_state(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(drive_files)")}
    assert {"mime_type", "synced_tags"} <= columns


def test_tag_tables_exist(tmp_path):
    conn = catalog.connect(tmp_path / "fresh.db")
    tags = {row["name"] for row in conn.execute("PRAGMA table_info(tags)")}
    file_tags = {row["name"] for row in conn.execute("PRAGMA table_info(file_tags)")}
    assert {"id", "name", "slug", "color"} <= tags
    assert {"drive_id", "tag_id"} <= file_tags


def test_upgrading_a_v2_catalog_matches_a_fresh_one(tmp_path):
    """The bug this guards: 'created new' and 'upgraded' drifting apart."""
    old = tmp_path / "old.db"
    conn = catalog.connect(old)
    conn.execute("INSERT INTO settings (key, value) VALUES ('photos_root', 'x')")
    conn.commit()
    # Strip everything added after v2 and rewind the version: a genuine v2 catalog.
    for table, column in STRIPPED_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DROP TABLE job_items")
    conn.execute("DROP TABLE file_tags")
    conn.execute("DROP TABLE tags")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    upgraded = catalog.connect(old)
    fresh = catalog.connect(tmp_path / "fresh.db")

    assert _schema(upgraded) == _schema(fresh)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 6
    assert upgraded.execute("SELECT value FROM settings").fetchone()["value"] == "x"


def test_the_v2_simulation_really_removes_the_later_columns(tmp_path):
    """Guards the guard.

    `test_upgrading_a_v2_catalog_matches_a_fresh_one` only proves anything if
    its simulated old catalog is genuinely missing the columns the migration
    adds. If the stripping list ever falls behind `_ADDED_COLUMNS` again, both
    sides get the columns from schema.sql and the comparison passes without
    exercising a single ALTER TABLE.
    """
    old = tmp_path / "old.db"
    conn = catalog.connect(old)
    for table, column in STRIPPED_COLUMNS:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute("DROP TABLE job_items")

    for table, column in STRIPPED_COLUMNS:
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert column not in present, f"{table}.{column} survived the strip"

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "job_items" not in tables
    conn.close()


def test_every_added_column_is_covered_by_the_strip_list():
    """A column in _ADDED_COLUMNS that no test strips is a column whose
    ALTER TABLE path is never run."""
    stripped = set(STRIPPED_COLUMNS)
    declared = {(table, column) for table, column, _ in migrations._ADDED_COLUMNS}
    assert declared - stripped == set()


def test_upgrading_keeps_existing_tags(tmp_path):
    """Migration must never be a data-loss event for hand-made tags."""
    db = tmp_path / "t.db"
    conn = catalog.connect(db)
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('drive-1', 1)")
    conn.commit()
    conn.close()

    upgraded = catalog.connect(db)
    assert upgraded.execute("SELECT slug FROM tags").fetchone()["slug"] == "family"
    assert upgraded.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 1


def test_migrating_twice_is_harmless(tmp_path):
    db = tmp_path / "t.db"
    catalog.connect(db).close()
    conn = catalog.connect(db)
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_an_upgraded_catalog_loses_the_place_column(conn):
    from photolib.db.migrations import migrate

    conn.execute("ALTER TABLE media ADD COLUMN place TEXT")
    migrate(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(media)")}
    assert "place" not in columns


def test_migrate_is_idempotent_about_dropped_columns(conn):
    from photolib.db.migrations import migrate

    migrate(conn)
    migrate(conn)   # a second run must not fail on the already-missing column

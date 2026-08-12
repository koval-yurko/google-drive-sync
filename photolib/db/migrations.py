"""Ordered schema upgrades.

`schema.sql` builds the current schema from nothing. The column list below
upgrades a catalog created by an earlier version. Both paths must arrive at
the same schema — `tests/test_migrations.py` asserts exactly that.

SQLite has no `ADD COLUMN IF NOT EXISTS`, so each addition checks
`PRAGMA table_info` first. That makes `migrate` idempotent and safe to run
against a catalog of any age.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 6
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# (table, column, full column definition)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("media", "upload_session_uri", "upload_session_uri TEXT"),
    ("media", "upload_offset", "upload_offset INTEGER NOT NULL DEFAULT 0"),
    ("media", "session_started_at", "session_started_at TEXT"),
    ("media", "attempts", "attempts INTEGER NOT NULL DEFAULT 0"),
    ("drive_files", "trashed_at", "trashed_at TEXT"),
    ("drive_files", "mime_type", "mime_type TEXT"),
    ("drive_files", "synced_tags", "synced_tags TEXT"),
    ("drive_files", "capture_hint", "capture_hint INTEGER"),
    ("jobs", "run_id", "run_id TEXT"),
    ("jobs", "resumed_from", "resumed_from TEXT"),
    ("jobs", "phase", "phase TEXT"),
    ("jobs", "items_done", "items_done INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "items_total", "items_total INTEGER NOT NULL DEFAULT 0"),
    ("media", "plan_verdict", "plan_verdict TEXT"),
    ("media", "plan_match", "plan_match TEXT"),
    ("drive_files", "country", "country TEXT"),
    ("drive_files", "latitude", "latitude REAL"),
    ("drive_files", "longitude", "longitude REAL"),
    ("drive_files", "metadata_source", "metadata_source TEXT"),
)

# (table, column) pairs retired from the schema. SQLite 3.35+ supports
# DROP COLUMN, and nothing indexes or references these.
_DROPPED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("media", "place"),
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """Bring any catalog — new, v1, v2 — up to the current schema."""
    conn.executescript(_SCHEMA_PATH.read_text())
    for table, column, definition in _ADDED_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    for table, column in _DROPPED_COLUMNS:
        if column in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()

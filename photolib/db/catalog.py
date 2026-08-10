"""SQLite connection handling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from photolib.db.migrations import SCHEMA_VERSION, migrate

__all__ = ["SCHEMA_VERSION", "connect"]


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the catalog, creating or migrating the schema as needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn

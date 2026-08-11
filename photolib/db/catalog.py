"""SQLite connection handling."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from photolib.db.migrations import SCHEMA_VERSION, migrate

__all__ = ["SCHEMA_VERSION", "connect"]


class LockedConnection(sqlite3.Connection):
    """A Connection carrying one RLock that every repo over it shares.

    Per-repository locks cannot provide mutual exclusion for a single
    physical connection used by multiple repo instances (JobsRepo,
    SettingsRepo, ...): two different lock objects guarding the same
    connection give no protection against each other. Attaching the lock
    to the connection itself, instead, means every repo that shares the
    connection shares the same lock.

    Attempting to set an arbitrary attribute on a plain sqlite3.Connection
    raises AttributeError, so the lock is defined on this subclass instead.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the catalog, creating or migrating the schema as needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path, check_same_thread=False, factory=LockedConnection
    )
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn

"""Key-value application settings stored in the catalog."""

from __future__ import annotations

import sqlite3
import threading

from pydantic import BaseModel

PHOTOS_ROOT = "photos_root"
ZIP_SOURCE = "zip_source"


class FolderRef(BaseModel):
    """A Drive folder chosen by the user."""

    id: str
    name: str


class SettingsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def all(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM settings")
        }

    def get_folder(self, key: str) -> FolderRef | None:
        raw = self.get(key)
        return FolderRef.model_validate_json(raw) if raw else None

    def set_folder(self, key: str, folder: FolderRef) -> None:
        self.set(key, folder.model_dump_json())

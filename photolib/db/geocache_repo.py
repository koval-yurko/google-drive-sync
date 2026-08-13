"""The reverse-geocoding cache.

A cached country of `None` means the API answered and found no country
there — a real result worth not re-requesting. `MISSING` is what a key
that was never looked up returns, so the two stay distinguishable.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Final


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


class GeocacheRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def get(self, key: str) -> str | None | _Missing:
        row = self._conn.execute(
            "SELECT country FROM geocache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return MISSING
        return row["country"]

    def put(self, key: str, country: str | None, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO geocache (key, country, raw_json) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET country = excluded.country, "
                "raw_json = excluded.raw_json",
                (key, country, json.dumps(payload)),
            )
            self._conn.commit()

"""Reverse-geocoding with a persistent cache.

Coordinates cluster heavily in this library, so rounding to roughly a kilometre
before caching turns hundreds of files into a handful of API calls. The API key
is optional: with none configured, place lookup degrades to no-op rather than
failing the surrounding work.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ENV_VAR = "GOOGLE_MAPS_API_KEY"
PLACE_TYPES = ("locality", "postal_town", "administrative_area_level_2")


def cache_key(lat: float, lon: float) -> str:
    """Round to ~1 km so nearby photos share one cache entry."""
    return f"{lat:.2f},{lon:.2f}"


def api_key_from_env(repo_root: Path | None = None) -> str | None:
    """The Geocoding API key from the environment, or from a `.env` file."""
    import os

    key = os.environ.get(ENV_VAR)
    if key:
        return key
    if repo_root is None:
        return None
    env_file = Path(repo_root) / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == ENV_VAR:
            return value.strip().strip("'\"") or None
    return None


class Geocoder:
    def __init__(self, conn: sqlite3.Connection, api_key: str | None, http=None) -> None:
        self._conn = conn
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def lookup(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        key = cache_key(lat, lon)
        cached = self._conn.execute(
            "SELECT place, country FROM geocache WHERE key = ?", (key,)
        ).fetchone()
        if cached is not None:
            return cached["place"], cached["country"]

        if not self._api_key:
            return None, None

        try:
            response = self._http.get(
                GEOCODE_URL,
                params={"latlng": f"{lat},{lon}", "key": self._api_key},
            )
            if not response.is_success:
                return None, None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None, None

        place, country = self._extract(payload)
        self._store(key, place, country, payload)
        return place, country

    @staticmethod
    def _extract(payload: dict) -> tuple[str | None, str | None]:
        place = country = None
        for result in payload.get("results", []):
            for component in result.get("address_components", []):
                types = component.get("types", [])
                if country is None and "country" in types:
                    country = component.get("long_name")
                if place is None and any(t in types for t in PLACE_TYPES):
                    place = component.get("long_name")
            if place and country:
                break
        return place, country

    def _store(self, key: str, place, country, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO geocache (key, place, country, raw_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET place = excluded.place, "
            "country = excluded.country, raw_json = excluded.raw_json",
            (key, place, country, json.dumps(payload)),
        )
        self._conn.commit()

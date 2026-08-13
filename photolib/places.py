"""Reverse-geocoding to a country, with a persistent cache.

Coordinates cluster heavily in this library, so rounding to roughly a kilometre
before caching turns hundreds of files into a handful of API calls. The API key
is optional: with none configured, the country lookup degrades to no-op rather
than failing the surrounding work.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from photolib.db.geocache_repo import MISSING, GeocacheRepo

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ENV_VAR = "GOOGLE_MAPS_API_KEY"


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
    def __init__(
        self, cache: GeocacheRepo, api_key: str | None, http=None
    ) -> None:
        self._cache = cache
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def lookup(self, lat: float, lon: float) -> str | None:
        """The country at these coordinates, or None."""
        key = cache_key(lat, lon)
        cached = self._cache.get(key)
        if cached is not MISSING:
            return cached

        if not self._api_key:
            return None

        try:
            response = self._http.get(
                GEOCODE_URL,
                params={"latlng": f"{lat},{lon}", "key": self._api_key},
            )
            if not response.is_success:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        country = self._extract(payload)
        self._cache.put(key, country, payload)
        return country

    @staticmethod
    def _extract(payload: dict) -> str | None:
        for result in payload.get("results", []):
            for component in result.get("address_components", []):
                if "country" in component.get("types", []):
                    return component.get("long_name")
        return None

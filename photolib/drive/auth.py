"""OAuth credential loading and refresh for the Drive API."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/drive"]


class MissingCredentialsError(Exception):
    """Raised when token.json is absent or unreadable."""


class TokenProvider:
    """Supplies a valid Drive access token, refreshing it when necessary."""

    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._creds: Credentials | None = None

    def is_configured(self) -> bool:
        return self._token_path.exists()

    def access_token(self) -> str:
        creds = self._load()
        if not creds.valid:
            creds.refresh(Request())
            self._persist(creds)
        return creds.token

    def _load(self) -> Credentials:
        if self._creds is not None:
            return self._creds
        if not self._token_path.exists():
            raise MissingCredentialsError(
                f"{self._token_path} not found; authorise the app first"
            )
        self._creds = Credentials.from_authorized_user_file(
            str(self._token_path), SCOPES
        )
        return self._creds

    def _persist(self, creds: Credentials) -> None:
        self._token_path.write_text(creds.to_json())

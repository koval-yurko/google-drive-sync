import json
import os
import time

import pytest

from photolib.drive.auth import MissingCredentialsError, TokenProvider

VALID_TOKEN = {
    "token": "cached-access-token",
    "refresh_token": "refresh-me",
    "token_uri": "https://oauth2.example/token",
    "client_id": "cid",
    "client_secret": "secret",
    "scopes": ["https://www.googleapis.com/auth/drive"],
    "expiry": "2099-01-01T00:00:00Z",
}


def write_token(tmp_path, payload):
    path = tmp_path / "token.json"
    path.write_text(json.dumps(payload))
    return path


def test_is_configured_false_when_files_absent(tmp_path):
    provider = TokenProvider(tmp_path / "creds.json", tmp_path / "token.json")
    assert provider.is_configured() is False


def test_is_configured_true_when_token_present(tmp_path):
    token = write_token(tmp_path, VALID_TOKEN)
    (tmp_path / "creds.json").write_text("{}")
    provider = TokenProvider(tmp_path / "creds.json", token)
    assert provider.is_configured() is True


def test_access_token_returns_unexpired_token(tmp_path):
    token = write_token(tmp_path, VALID_TOKEN)
    provider = TokenProvider(tmp_path / "creds.json", token)
    assert provider.access_token() == "cached-access-token"


def test_access_token_refreshes_when_expired(tmp_path, monkeypatch):
    expired = {**VALID_TOKEN, "expiry": "2000-01-01T00:00:00Z"}
    token = write_token(tmp_path, expired)
    provider = TokenProvider(tmp_path / "creds.json", token)

    def fake_refresh(self, request):
        self.token = "fresh-access-token"
        self.expiry = None

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.refresh", fake_refresh
    )
    assert provider.access_token() == "fresh-access-token"
    assert json.loads(token.read_text())["token"] == "fresh-access-token"


def test_access_token_raises_when_missing(tmp_path):
    provider = TokenProvider(tmp_path / "creds.json", tmp_path / "token.json")
    with pytest.raises(MissingCredentialsError):
        provider.access_token()


def test_cache_reused_on_unchanged_file(tmp_path, monkeypatch):
    """Cache is reused: two access_token() calls on unchanged file read it once."""
    token = write_token(tmp_path, VALID_TOKEN)
    provider = TokenProvider(tmp_path / "creds.json", token)

    load_count = 0
    original_from_file = None

    def counting_from_file(path, scopes):
        nonlocal load_count, original_from_file
        load_count += 1
        # Call the original method
        return original_from_file(path, scopes)

    # Patch with a wrapper around the original
    from google.oauth2 import credentials as creds_module
    original_from_file = creds_module.Credentials.from_authorized_user_file
    monkeypatch.setattr(
        creds_module.Credentials,
        "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: counting_from_file(path, scopes)),
    )

    first_token = provider.access_token()
    assert first_token == "cached-access-token"
    assert load_count == 1

    second_token = provider.access_token()
    assert second_token == "cached-access-token"
    assert load_count == 1  # File not re-read


def test_cache_reloaded_on_file_change(tmp_path, monkeypatch):
    """Cache is invalidated: external token file change causes reload."""
    token = write_token(tmp_path, VALID_TOKEN)
    provider = TokenProvider(tmp_path / "creds.json", token)

    first_token = provider.access_token()
    assert first_token == "cached-access-token"

    # Externally modify token file with a new token
    new_payload = {**VALID_TOKEN, "token": "new-external-token"}
    token.write_text(json.dumps(new_payload))
    # Set a clearly later mtime so it's detected
    later_time = time.time() + 10
    os.utime(str(token), (later_time, later_time))

    second_token = provider.access_token()
    assert second_token == "new-external-token"


def test_access_token_raises_when_file_deleted(tmp_path):
    """File deletion between calls raises MissingCredentialsError."""
    token = write_token(tmp_path, VALID_TOKEN)
    provider = TokenProvider(tmp_path / "creds.json", token)

    first_token = provider.access_token()
    assert first_token == "cached-access-token"

    # Delete the token file externally
    token.unlink()

    with pytest.raises(MissingCredentialsError):
        provider.access_token()

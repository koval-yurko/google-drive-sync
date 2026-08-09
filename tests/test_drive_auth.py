import json

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

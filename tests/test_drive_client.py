import httpx
import pytest

from photolib.drive.client import DriveClient
from photolib.drive.errors import NotFoundError, RateLimitedError, retry


class StubTokens:
    def access_token(self) -> str:
        return "test-token"

    def is_configured(self) -> bool:
        return True


def client_with(handler) -> DriveClient:
    transport = httpx.MockTransport(handler)
    return DriveClient(StubTokens(), http=httpx.Client(transport=transport))


def test_get_file_parses_metadata():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={
            "id": "f1", "name": "photo.HEIC", "mimeType": "image/heic",
            "size": "1234", "md5Checksum": "abc", "parents": ["p1"],
            "modifiedTime": "2026-01-01T00:00:00Z",
        })

    file = client_with(handler).get_file("f1")
    assert file.id == "f1"
    assert file.name == "photo.HEIC"
    assert file.size == 1234
    assert file.md5 == "abc"
    assert file.parents == ["p1"]
    assert file.is_folder is False


def test_folder_detection():
    def handler(request):
        return httpx.Response(200, json={
            "id": "d1", "name": "Photos",
            "mimeType": "application/vnd.google-apps.folder", "parents": [],
        })

    assert client_with(handler).get_file("d1").is_folder is True


def test_list_children_follows_pagination():
    pages = [
        {"files": [{"id": "a", "name": "A", "mimeType": "image/jpeg"}],
         "nextPageToken": "tok2"},
        {"files": [{"id": "b", "name": "B", "mimeType": "image/jpeg"}]},
    ]
    calls = []

    def handler(request):
        calls.append(request.url.params.get("pageToken"))
        return httpx.Response(200, json=pages[len(calls) - 1])

    files = client_with(handler).list_children("parent")
    assert [f.id for f in files] == ["a", "b"]
    assert calls == [None, "tok2"]


def test_list_children_folders_only_filters_query():
    seen = {}

    def handler(request):
        seen["q"] = request.url.params["q"]
        return httpx.Response(200, json={"files": []})

    client_with(handler).list_children("parent", folders_only=True)
    assert "application/vnd.google-apps.folder" in seen["q"]
    assert "'parent' in parents" in seen["q"]
    assert "trashed = false" in seen["q"]


def test_read_range_sends_inclusive_range_header():
    seen = {}

    def handler(request):
        seen["range"] = request.headers["Range"]
        seen["alt"] = request.url.params.get("alt")
        return httpx.Response(206, content=b"0123456789")

    data = client_with(handler).read_range("f1", 100, 109)
    assert data == b"0123456789"
    assert seen["range"] == "bytes=100-109"
    assert seen["alt"] == "media"


def test_missing_file_raises_not_found():
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "File not found"}})

    with pytest.raises(NotFoundError):
        client_with(handler).get_file("nope")


def test_rate_limit_raises_rate_limited():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(RateLimitedError):
        client_with(handler).get_file("f1")


def test_retry_recovers_after_transient_failures(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)
    attempts = {"n": 0}

    @retry
    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimitedError("busy")
        return "ok"

    assert flaky() == "ok"
    assert attempts["n"] == 3


def test_retry_gives_up_and_reraises(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)

    @retry
    def always_busy():
        raise RateLimitedError("busy")

    with pytest.raises(RateLimitedError):
        always_busy()


def test_retry_does_not_retry_not_found(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)
    attempts = {"n": 0}

    @retry
    def missing():
        attempts["n"] += 1
        raise NotFoundError("gone")

    with pytest.raises(NotFoundError):
        missing()
    assert attempts["n"] == 1

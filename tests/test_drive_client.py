import httpx
import pytest

from photolib.drive.client import DriveClient, DriveFile
from photolib.drive.errors import DriveError, MAX_ATTEMPTS, NotFoundError, RateLimitedError, retry


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


def test_rate_limit_raises_rate_limited(monkeypatch):
    monkeypatch.setattr("photolib.drive.errors.time.sleep", lambda _: None)

    def handler(request):
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(RateLimitedError):
        client_with(handler).get_file("f1")


def test_403_with_rate_in_message_raises_rate_limited():
    from photolib.drive.errors import raise_for_response

    response = httpx.Response(403, json={"error": {"message": "Rate Limit Exceeded"}})
    with pytest.raises(RateLimitedError):
        raise_for_response(response)


def test_403_without_rate_raises_drive_error():
    from photolib.drive.errors import raise_for_response

    response = httpx.Response(403, json={"error": {"message": "The user does not have sufficient permissions for this file"}})
    with pytest.raises(DriveError) as exc_info:
        raise_for_response(response)
    assert not isinstance(exc_info.value, RateLimitedError)


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
    attempts = {"n": 0}

    @retry
    def always_busy():
        attempts["n"] += 1
        raise RateLimitedError("busy")

    with pytest.raises(RateLimitedError):
        always_busy()
    assert attempts["n"] == MAX_ATTEMPTS


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


def test_file_fields_request_the_thumbnail_link():
    from photolib.drive.client import FILE_FIELDS
    assert "thumbnailLink" in FILE_FIELDS


def test_fetch_thumbnail_rewrites_the_size_suffix():
    """Drive hands back =s220; the grid wants =s400 and the lightbox =s1600."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/f1"):
            return httpx.Response(
                200,
                json={
                    "id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic",
                    "thumbnailLink": "https://lh3.example/abc=s220",
                },
            )
        requested.append(str(request.url))
        return httpx.Response(200, content=b"jpegbytes")

    client = client_with(handler)
    assert client.fetch_thumbnail("f1", 400) == b"jpegbytes"
    assert requested == ["https://lh3.example/abc=s400"]


def test_fetch_thumbnail_appends_a_size_when_the_link_has_none():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/f1"):
            return httpx.Response(
                200,
                json={
                    "id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic",
                    "thumbnailLink": "https://lh3.example/abc",
                },
            )
        assert str(request.url) == "https://lh3.example/abc=s400"
        return httpx.Response(200, content=b"jpegbytes")

    assert client_with(handler).fetch_thumbnail("f1", 400) == b"jpegbytes"


def test_fetch_thumbnail_is_none_when_drive_has_not_made_one():
    """Freshly uploaded files have no thumbnailLink for a few minutes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic"}
        )

    assert client_with(handler).fetch_thumbnail("f1", 400) is None


def test_app_properties_returns_an_empty_dict_when_there_are_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert client_with(handler).app_properties("f1") == {}


def test_app_properties_returns_what_drive_holds():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fields"] == "appProperties"
        return httpx.Response(200, json={"appProperties": {"t_family": "1"}})

    assert client_with(handler).app_properties("f1") == {"t_family": "1"}


def test_capture_hint_prefers_exif_time():
    file = DriveFile(
        id="x", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"time": "2024:01:13 10:00:00"},
        modifiedTime="2026-01-01T00:00:00Z",
    )
    assert file.capture_hint() == 1705140000   # 2024-01-13T10:00:00Z


def test_capture_hint_falls_back_to_modified_time():
    file = DriveFile(
        id="x", name="a.mov", mimeType="video/quicktime",
        modifiedTime="2024-01-13T10:00:00Z",
    )
    assert file.capture_hint() == 1705140000


def test_capture_hint_survives_malformed_exif():
    file = DriveFile(
        id="x", name="a.heic", mimeType="image/heic",
        imageMediaMetadata={"time": "not a timestamp"},
        modifiedTime="2024-01-13T10:00:00Z",
    )
    assert file.capture_hint() == 1705140000


def test_capture_hint_of_an_undated_file_is_none():
    assert DriveFile(id="x", name="a", mimeType="image/heic").capture_hint() is None

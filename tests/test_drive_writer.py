"""DriveWriter against a scripted transport — no network, real HTTP semantics."""

import json

import httpx
import pytest

from photolib.drive.client import DriveClient
from photolib.drive.errors import DriveError
from photolib.drive.writer import DriveWriter, SessionExpiredError


class Tokens:
    def access_token(self) -> str:
        return "test-token"


def writer_for(handler) -> DriveWriter:
    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    return DriveWriter(DriveClient(Tokens(), http=http))


def test_create_folder_posts_the_right_metadata():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={
            "id": "new", "name": "2023-11",
            "mimeType": "application/vnd.google-apps.folder",
        })

    folder = writer_for(handler).create_folder("root-id", "2023-11")
    assert folder.id == "new"
    assert folder.is_folder
    assert seen["body"]["parents"] == ["root-id"]
    assert seen["body"]["mimeType"] == "application/vnd.google-apps.folder"
    assert seen["auth"] == "Bearer test-token"


def test_start_session_returns_the_location_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Upload-Content-Length"] == "1024"
        body = json.loads(request.content)
        assert body["appProperties"] == {"source_crc": "abc"}
        return httpx.Response(200, headers={"Location": "https://upload/session/1"})

    uri = writer_for(handler).start_session(
        parent_id="p", name="IMG.HEIC", size=1024,
        mime_type="image/heic", properties={"source_crc": "abc"},
    )
    assert uri == "https://upload/session/1"


def test_start_session_without_a_location_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    with pytest.raises(DriveError, match="Location"):
        writer_for(handler).start_session(
            parent_id="p", name="x", size=1, mime_type="x/y", properties={}
        )


def test_session_offset_reads_the_range_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Range"] == "bytes */900"
        return httpx.Response(308, headers={"Range": "bytes=0-511"})

    assert writer_for(handler).session_offset("https://upload/s", 900) == 512


def test_session_offset_of_a_fresh_session_is_zero():
    """Drive omits the Range header when it has received nothing."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(308)

    assert writer_for(handler).session_offset("https://upload/s", 900) == 0


def test_session_offset_of_a_finished_upload_is_the_whole_size():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "f", "name": "n", "mimeType": "x/y"})

    assert writer_for(handler).session_offset("https://upload/s", 900) == 900


def test_a_forgotten_session_raises_session_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "gone"}})

    with pytest.raises(SessionExpiredError):
        writer_for(handler).session_offset("https://upload/s", 900)


def test_send_chunk_returns_none_until_the_last_chunk():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Range"] == "bytes 0-3/8"
        return httpx.Response(308, headers={"Range": "bytes=0-3"})

    assert writer_for(handler).send_chunk("https://upload/s", b"abcd", 0, 8) is None


def test_send_chunk_returns_the_file_when_complete():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Range"] == "bytes 4-7/8"
        return httpx.Response(200, json={
            "id": "f1", "name": "IMG.HEIC", "mimeType": "image/heic",
            "md5Checksum": "deadbeef", "size": "8",
        })

    file = writer_for(handler).send_chunk("https://upload/s", b"efgh", 4, 8)
    assert file is not None
    assert (file.id, file.md5) == ("f1", "deadbeef")


def test_trash_patches_rather_than_deleting():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "f1", "name": "n", "mimeType": "x/y"})

    writer_for(handler).trash("f1")
    assert seen["method"] == "PATCH"
    assert seen["body"] == {"trashed": True}


from photolib.drive.errors import TransientError
from tests.fakes.fake_drive import FakeDrive


def test_fake_assembles_chunks_into_a_file_with_a_real_md5():
    import hashlib

    fake = FakeDrive()
    fake.add_folder("p", "parent")
    uri = fake.start_session("p", "IMG.HEIC", 8, "image/heic", {"source_crc": "1"})
    assert fake.send_chunk(uri, b"abcd", 0, 8) is None
    assert fake.session_offset(uri, 8) == 4
    file = fake.send_chunk(uri, b"efgh", 4, 8)
    assert file is not None
    assert file.md5 == hashlib.md5(b"abcdefgh").hexdigest()
    assert fake.get_file(file.id).name == "IMG.HEIC"


def test_fake_rejects_a_chunk_at_the_wrong_offset():
    fake = FakeDrive()
    fake.add_folder("p", "parent")
    uri = fake.start_session("p", "x", 8, "x/y", {})
    with pytest.raises(DriveError):
        fake.send_chunk(uri, b"efgh", 4, 8)      # nothing sent yet; 4 is wrong


def test_fake_can_expire_a_session():
    fake = FakeDrive()
    fake.add_folder("p", "parent")
    uri = fake.start_session("p", "x", 4, "x/y", {})
    fake.expire_session(uri)
    with pytest.raises(SessionExpiredError):
        fake.session_offset(uri, 4)


def test_fake_can_fail_chunks_transiently():
    fake = FakeDrive()
    fake.add_folder("p", "parent")
    uri = fake.start_session("p", "x", 4, "x/y", {})
    fake.fail_chunks = 1
    with pytest.raises(TransientError):
        fake.send_chunk(uri, b"abcd", 0, 4)
    assert fake.send_chunk(uri, b"abcd", 0, 4) is not None


def test_fake_creates_distinct_folders_for_the_same_name():
    """Drive really does allow this, and it is how a library gets split in two."""
    fake = FakeDrive()
    fake.add_folder("p", "parent")
    first = fake.create_folder("p", "2023-11")
    second = fake.create_folder("p", "2023-11")
    assert first.id != second.id
    # ensure_folder is what protects against it
    assert fake.ensure_folder("p", "2023-11").id == first.id


def test_fake_trash_hides_the_file():
    fake = FakeDrive()
    fake.add_folder("p", "parent")
    fake.add_file("f1", "x.HEIC", b"x", parent="p")
    fake.trash("f1")
    assert fake.trashed == ["f1"]
    assert fake.list_children("p") == []

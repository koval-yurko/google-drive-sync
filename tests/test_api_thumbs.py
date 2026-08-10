import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"heic", parent="root")
    fake.set_thumbnail("d1", b"jpeg")
    fake.add_file("d2", "IMG_2.HEIC", b"heic", parent="root")   # not rendered yet
    return fake


@pytest.fixture
def client(tmp_path, drive):
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    with TestClient(create_app(config=config, drive=drive)) as test_client:
        yield test_client


def test_serves_jpeg_bytes(client):
    response = client.get("/api/thumb/d1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-s400"


def test_defaults_to_the_grid_size(client, drive):
    client.get("/api/thumb/d1")
    assert drive.thumbnail_requests == [("d1", 400)]


def test_the_lightbox_size_is_available(client):
    assert client.get("/api/thumb/d1?size=1600").content == b"jpeg-s1600"


def test_an_arbitrary_size_is_refused(client):
    assert client.get("/api/thumb/d1?size=64").status_code == 422


def test_a_second_request_is_served_from_disk(client, drive):
    client.get("/api/thumb/d1")
    drive.thumbnail_requests.clear()
    assert client.get("/api/thumb/d1").content == b"jpeg-s400"
    assert drive.thumbnail_requests == []


def test_a_file_drive_has_not_rendered_yet_returns_202(client):
    """202 tells the tile to show a placeholder and try again, not to break."""
    assert client.get("/api/thumb/d2").status_code == 202


def test_an_unknown_file_is_404(client):
    assert client.get("/api/thumb/nosuchfile").status_code == 404

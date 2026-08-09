import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    drive = FakeDrive()
    drive.add_folder("photos", "Global Photos")
    drive.add_folder("zips", "zip-3-22-26")
    app = create_app(config=Config.load(), drive=drive)
    with TestClient(app) as c:
        yield c


def test_settings_start_empty(client):
    body = client.get("/api/settings").json()
    assert body["photos_root"] is None
    assert body["zip_source"] is None
    assert body["credentials_configured"] is False


def test_put_and_get_photos_root(client):
    response = client.put(
        "/api/settings/photos_root", json={"id": "photos", "name": "Global Photos"}
    )
    assert response.status_code == 200
    assert response.json() == {"id": "photos", "name": "Global Photos"}
    assert client.get("/api/settings").json()["photos_root"]["id"] == "photos"


def test_put_zip_source(client):
    client.put("/api/settings/zip_source", json={"id": "zips", "name": "zip-3-22-26"})
    assert client.get("/api/settings").json()["zip_source"]["name"] == "zip-3-22-26"


def test_unknown_setting_key_is_rejected(client):
    response = client.put("/api/settings/hack", json={"id": "x", "name": "y"})
    assert response.status_code == 400


def test_settings_persist_across_app_instances(client, tmp_path):
    client.put("/api/settings/photos_root", json={"id": "photos", "name": "P"})
    from photolib.api.app import create_app as make

    second = make(config=Config.load(), drive=FakeDrive())
    with TestClient(second) as c2:
        assert c2.get("/api/settings").json()["photos_root"]["id"] == "photos"

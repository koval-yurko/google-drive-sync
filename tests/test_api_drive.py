import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    drive = FakeDrive()
    drive.add_folder("root", "My Drive")
    drive.add_folder("photos", "Global Photos", parent="root")
    drive.add_folder("zips", "zip-3-22-26", parent="root")
    drive.add_folder("nested", "Takeout", parent="zips")
    drive.add_file("z1", "takeout-001.zip", build_zip({"a.txt": b"x"}), parent="zips")
    app = create_app(config=Config.load(), drive=drive)
    with TestClient(app) as c:
        yield c


def test_lists_root_folders_by_default(client):
    body = client.get("/api/drive/folders").json()
    names = [f["name"] for f in body["folders"]]
    assert "Global Photos" in names
    assert "zip-3-22-26" in names


def test_lists_child_folders(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert [f["name"] for f in body["folders"]] == ["Takeout"]


def test_files_are_excluded_from_folder_listing(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert all(f["name"] != "takeout-001.zip" for f in body["folders"])


def test_includes_parent_details(client):
    body = client.get("/api/drive/folders", params={"parent": "zips"}).json()
    assert body["parent"]["name"] == "zip-3-22-26"


def test_unknown_parent_returns_404(client):
    assert client.get("/api/drive/folders", params={"parent": "ghost"}).status_code == 404

import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path):
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )
    app = create_app(config=config, drive=FakeDrive())
    with TestClient(app) as test_client:
        conn = app.state.conn
        for drive_id in ("d1", "d2"):
            conn.execute(
                "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
                "VALUES (?, 'IMG.HEIC', '2025-05', 'md5', 100, 'image/heic')",
                (drive_id,),
            )
        conn.commit()
        yield test_client


def test_creating_a_tag_returns_201_and_the_row(client):
    response = client.post("/api/tags", json={"name": "Family"})
    assert response.status_code == 201
    assert response.json()["slug"] == "family"
    assert response.json()["file_count"] == 0


def test_creating_a_duplicate_is_409(client):
    client.post("/api/tags", json={"name": "Family"})
    assert client.post("/api/tags", json={"name": "family"}).status_code == 409


def test_creating_a_nameless_tag_is_422(client):
    assert client.post("/api/tags", json={"name": "   "}).status_code == 422


def test_listing_reports_counts(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    body = client.get("/api/tags").json()
    assert body == [
        {"id": tag["id"], "name": "Family", "slug": "family",
         "color": "#6b7280", "file_count": 2}
    ]


def test_adding_files_reports_how_many_were_new(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    first = client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1"]})
    second = client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    assert first.json() == {"added": 1}
    assert second.json() == {"added": 1}


def test_removing_files(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1", "d2"]})

    response = client.post(
        f"/api/tags/{tag['id']}/files/remove", json={"drive_ids": ["d1"]}
    )
    assert response.json() == {"removed": 1}
    assert client.get("/api/tags").json()[0]["file_count"] == 1


def test_tagging_an_unknown_tag_is_404(client):
    assert client.post("/api/tags/999/files", json={"drive_ids": ["d1"]}).status_code == 404


def test_renaming(client):
    tag = client.post("/api/tags", json={"name": "Familly"}).json()
    body = client.patch(f"/api/tags/{tag['id']}", json={"name": "Family"}).json()
    assert (body["name"], body["slug"]) == ("Family", "family")


def test_renaming_onto_an_existing_name_is_409(client):
    client.post("/api/tags", json={"name": "Family"})
    other = client.post("/api/tags", json={"name": "Friends"}).json()
    assert client.patch(f"/api/tags/{other['id']}", json={"name": "Family"}).status_code == 409


def test_recolouring(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    body = client.patch(f"/api/tags/{tag['id']}", json={"color": "#ff0000"}).json()
    assert body["color"] == "#ff0000"


def test_patching_nothing_is_422(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    assert client.patch(f"/api/tags/{tag['id']}", json={}).status_code == 422


def test_deleting_a_tag_removes_its_assignments(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{tag['id']}/files", json={"drive_ids": ["d1"]})

    assert client.delete(f"/api/tags/{tag['id']}").status_code == 200
    assert client.get("/api/tags").json() == []
    assert client.get("/api/library/files?tag_id=%d" % tag["id"]).json()["total"] == 0


def test_merging(client):
    source = client.post("/api/tags", json={"name": "Familly"}).json()
    target = client.post("/api/tags", json={"name": "Family"}).json()
    client.post(f"/api/tags/{source['id']}/files", json={"drive_ids": ["d1", "d2"]})
    client.post(f"/api/tags/{target['id']}/files", json={"drive_ids": ["d2"]})

    body = client.post(
        "/api/tags/merge", json={"source_id": source["id"], "target_id": target["id"]}
    ).json()

    assert body["moved"] == 1
    assert [t["slug"] for t in client.get("/api/tags").json()] == ["family"]
    assert client.get("/api/tags").json()[0]["file_count"] == 2


def test_merging_a_tag_into_itself_is_422(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    response = client.post(
        "/api/tags/merge", json={"source_id": tag["id"], "target_id": tag["id"]}
    )
    assert response.status_code == 422


def test_merging_an_unknown_tag_is_404(client):
    tag = client.post("/api/tags", json={"name": "Family"}).json()
    response = client.post(
        "/api/tags/merge", json={"source_id": 999, "target_id": tag["id"]}
    )
    assert response.status_code == 404

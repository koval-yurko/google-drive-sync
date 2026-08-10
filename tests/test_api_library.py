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
    )
    app = create_app(config=config, drive=FakeDrive())
    with TestClient(app) as test_client:
        conn = app.state.conn
        for drive_id, name, month, mime in [
            ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
            ("d2", "VID_1.MOV", "2025-06", "video/quicktime"),
        ]:
            conn.execute(
                "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
                "VALUES (?, ?, ?, 'md5', 100, ?)",
                (drive_id, name, month, mime),
            )
        conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
        conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d1', 1)")
        conn.commit()
        yield test_client


def test_files_lists_everything(client):
    body = client.get("/api/library/files").json()
    assert body["total"] == 2
    assert {row["drive_id"] for row in body["rows"]} == {"d1", "d2"}


def test_files_carry_their_tags(client):
    rows = {r["drive_id"]: r for r in client.get("/api/library/files").json()["rows"]}
    assert [t["slug"] for t in rows["d1"]["tags"]] == ["family"]
    assert rows["d2"]["tags"] == []


def test_files_filter_by_month(client):
    body = client.get("/api/library/files?month=2025-06").json()
    assert [row["drive_id"] for row in body["rows"]] == ["d2"]


def test_files_filter_by_type(client):
    body = client.get("/api/library/files?media_type=video").json()
    assert body["total"] == 1


def test_files_filter_by_tag(client):
    body = client.get("/api/library/files?tag_id=1").json()
    assert [row["drive_id"] for row in body["rows"]] == ["d1"]


def test_files_reject_an_oversized_page(client):
    assert client.get("/api/library/files?limit=5000").status_code == 422


def test_ids_returns_the_whole_filtered_set(client):
    assert client.get("/api/library/ids?month=2025-05").json() == {"ids": ["d1"]}


def test_facets_report_each_dimension(client):
    body = client.get("/api/library/facets").json()
    assert body["total"] == 2
    assert body["months"] == [
        {"value": "2025-06", "count": 1},
        {"value": "2025-05", "count": 1},
    ]
    assert body["duplicates"] == 0


def test_file_detail_includes_tags(client):
    body = client.get("/api/library/file/d1").json()
    assert body["name"] == "IMG_1.HEIC"
    assert [t["slug"] for t in body["tags"]] == ["family"]


def test_file_detail_404s_for_an_unknown_id(client):
    assert client.get("/api/library/file/nope").status_code == 404

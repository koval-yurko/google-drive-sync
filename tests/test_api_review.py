import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from photolib.db.media_repo import MediaRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
        conn = app.state.conn
        conn.execute(
            "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
        )
        conn.executemany(
            "INSERT INTO entries (archive_id, path, name, crc32, size,"
            " compressed_size, method, local_header_offset, kind)"
            " VALUES (1,?,?,1,10,5,8,0,'media')",
            [("d/IMG_1.HEIC", "IMG_1.HEIC"), ("d/IMG_2.MOV", "IMG_2.MOV")],
        )
        conn.commit()
        repo = MediaRepo(conn)
        repo.upsert_media(1)
        repo.upsert_media(2)
        repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC",
                      capture_source="photo_taken_time", place="Warsaw")
        repo.set_plan(2, target_folder="2019-01", target_name="IMG_2.MOV",
                      capture_source="year_folder",
                      duplicate_of="back_2024_01", duplicate_reason="name match")
        yield c


def test_summary_reports_totals(client):
    body = client.get("/api/review/summary").json()
    assert body["media"] == 2
    assert body["planned"] == 2
    assert body["duplicates"] == 1
    assert body["with_place"] == 1


def test_media_returns_every_row(client):
    body = client.get("/api/review/media").json()
    assert body["total"] == 2
    assert {r["name"] for r in body["rows"]} == {"IMG_1.HEIC", "IMG_2.MOV"}


def test_media_pagination(client):
    body = client.get("/api/review/media", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 2
    assert len(body["rows"]) == 1


def test_media_filters_by_folder(client):
    body = client.get("/api/review/media", params={"folder": "2019-01"}).json()
    assert [r["name"] for r in body["rows"]] == ["IMG_2.MOV"]
    assert body["total"] == 1


def test_media_filters_duplicates_only(client):
    body = client.get("/api/review/media", params={"duplicates_only": "true"}).json()
    assert [r["name"] for r in body["rows"]] == ["IMG_2.MOV"]


def test_rows_never_report_skipped(client):
    body = client.get("/api/review/media").json()
    assert {r["upload_status"] for r in body["rows"]} == {"pending"}

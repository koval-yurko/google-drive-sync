import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    """A client whose Drive is wired up enough for `sync_archives` to
    complete: one archive to read from, an empty destination to upload to."""
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file(
        "z1", "takeout-001.zip",
        build_zip({
            "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC":
                b"pretend this is a photograph" * 100,
        }),
        parent="zips",
    )
    drive.add_folder("photos", "Photos")
    app = create_app(config=Config.load(), drive=drive)
    with TestClient(app) as c:
        c.put("/api/settings/zip_source", json={"id": "zips", "name": "zip-source"})
        c.put("/api/settings/photos_root", json={"id": "photos", "name": "Photos"})
        yield c


def test_lists_actions_with_schema(client):
    actions = client.get("/api/actions").json()
    assert any(a["id"] == "check_connection" for a in actions)
    spec = next(a for a in actions if a["id"] == "check_connection")
    assert spec["title"] == "Check Connection"
    assert spec["description"]
    assert spec["schema"]["type"] == "object"


def test_run_creates_a_job(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    assert job["action"] == "check_connection"
    assert job["status"] in {"queued", "running", "done"}
    assert job["id"]


def test_run_unknown_action_returns_404(client):
    assert client.post("/api/actions/nope/run", json={}).status_code == 404


def test_run_with_unknown_params_returns_422(client):
    response = client.post("/api/actions/check_connection/run", json={"bad": 1})
    assert response.status_code == 422


def test_action_list_exposes_the_group(client):
    body = client.get("/api/actions").json()
    assert all("group" in spec for spec in body)


def test_confirming_a_flow_through_the_route_reaches_upload(sync_client):
    """The seam the runner-level run_id fix closes: an operator reads the
    run_id off the unconfirmed job and posts it back with confirm=true,
    exactly as the frontend will — no explicit run_id argument to submit()."""
    unconfirmed = sync_client.post("/api/actions/sync_archives/run", json={}).json()
    sync_client.app.state.runner.wait_idle()
    run_id = unconfirmed["run_id"]
    assert run_id  # sanity: JobsRepo.create always mints one

    confirmed = sync_client.post(
        "/api/actions/sync_archives/run",
        json={"confirm": True, "run_id": run_id},
    ).json()
    assert confirmed["run_id"] == run_id
    sync_client.app.state.runner.wait_idle()

    final = sync_client.get(f"/api/jobs/{confirmed['id']}").json()
    assert final["status"] == "done"
    assert final["phase"] and final["phase"].startswith("Upload")

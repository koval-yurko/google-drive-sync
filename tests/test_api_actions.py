import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    app = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app) as c:
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

import time

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


def finished_job(client) -> dict:
    job = client.post("/api/actions/check_connection/run", json={}).json()
    for _ in range(100):
        current = client.get(f"/api/jobs/{job['id']}").json()
        if current["status"] in {"done", "failed"}:
            return current
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_job_reaches_a_terminal_state(client):
    assert finished_job(client)["status"] == "done"


def test_job_list_includes_the_run(client):
    job = finished_job(client)
    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert job["id"] in ids


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/missing").status_code == 404


def test_events_are_returned_and_filterable(client):
    job = finished_job(client)
    events = client.get(f"/api/jobs/{job['id']}/events").json()
    assert events
    after = client.get(
        f"/api/jobs/{job['id']}/events", params={"after": events[0]["id"]}
    ).json()
    assert len(after) == len(events) - 1


def test_stream_endpoint_serves_event_stream(client):
    job = finished_job(client)
    with client.stream("GET", f"/api/jobs/{job['id']}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


def test_cancel_route_rejects_a_finished_job(client):
    job = client.post("/api/actions/check_connection/run", json={}).json()
    client.app.state.runner.wait_idle()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_cancel_route_404s_on_an_unknown_job(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404

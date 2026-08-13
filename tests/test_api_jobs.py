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
    job = client.post("/api/actions/verify_library/run", json={}).json()
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
    job = client.post("/api/actions/verify_library/run", json={}).json()
    client.app.state.runner.wait_idle()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_cancel_route_404s_on_an_unknown_job(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_resume_reuses_the_run_id_and_records_the_source(client):
    job = client.post("/api/actions/verify_library/run", json={}).json()
    client.app.state.runner.wait_idle()
    client.app.state.jobs.mark_failed(job["id"], "boom")

    resumed = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert resumed["run_id"] == job["run_id"]
    assert resumed["resumed_from"] == job["id"]
    assert resumed["id"] != job["id"]


def test_resume_rejects_a_successful_job(client):
    job = client.post("/api/actions/verify_library/run", json={}).json()
    client.app.state.runner.wait_idle()
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 409


def test_resume_injects_run_id_only_when_the_action_declares_it(client):
    """verify_library has no run_id param; extra='forbid' would reject it."""
    job = client.post("/api/actions/verify_library/run", json={}).json()
    client.app.state.runner.wait_idle()
    client.app.state.jobs.mark_failed(job["id"], "boom")
    resumed = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert "run_id" not in resumed["params"]


def test_a_job_left_running_is_failed_on_the_next_app_start_and_then_resumable(
    tmp_path, monkeypatch
):
    """I3: nothing watches a `running` job across a restart, and only
    failed/cancelled jobs are resumable (RESUMABLE, above) — without
    reconciling at startup, a job the previous process left `running` is
    stuck forever. Two separate `create_app` calls against the same
    on-disk database stand in for "the process restarted"."""
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))

    app1 = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app1) as c1:
        job = c1.post("/api/actions/verify_library/run", json={}).json()
        c1.app.state.runner.wait_idle()
        # Force it back to 'running', standing in for a process that died
        # mid-job rather than one that finished normally.
        c1.app.state.jobs.mark_running(job["id"])
        assert c1.get(f"/api/jobs/{job['id']}").json()["status"] == "running"

    app2 = create_app(config=Config.load(), drive=FakeDrive())
    with TestClient(app2) as c2:
        reloaded = c2.get(f"/api/jobs/{job['id']}").json()
        assert reloaded["status"] == "failed"
        assert "interrupted" in reloaded["error"]

        resumed = c2.post(f"/api/jobs/{job['id']}/resume").json()
        assert resumed["run_id"] == reloaded["run_id"]
        assert resumed["resumed_from"] == job["id"]


def test_items_route_returns_the_ledger(client):
    from photolib.db.job_items_repo import JobItemsRepo

    job = client.post("/api/actions/verify_library/run", json={}).json()
    client.app.state.runner.wait_idle()
    JobItemsRepo(client.app.state.conn).enumerate(
        job["run_id"], "work", ["a", "b"], job["id"]
    )
    body = client.get(f"/api/jobs/{job['id']}/items?phase=work").json()
    assert [i["item_key"] for i in body] == ["a", "b"]

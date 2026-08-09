import pytest

from photolib.actions.base import ActionContext, ProgressEvent
from photolib.config import Config
from photolib.db.jobs_repo import JobsRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.jobs.broker import EventBroker
from photolib.jobs.runner import JobRunner
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def runner(conn):
    repo = JobsRepo(conn)
    broker = EventBroker()

    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=FakeDrive(), settings=SettingsRepo(conn),
            config=Config.load(),
        )

    r = JobRunner(context_factory=context_factory, repo=repo, broker=broker)
    r.start()
    yield r
    r.stop()


def test_runs_a_job_to_completion(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert JobsRepo(conn).get(job.id).status == "done"


def test_records_events_from_the_action(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    events = JobsRepo(conn).events(job.id)
    assert events
    assert all(e.message for e in events)


def test_failure_is_captured(runner, conn, monkeypatch):
    def explode(ctx, params):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    from photolib.actions import registry

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(
        registry, "get_action", lambda _id: type(spec)(
            id=spec.id, title=spec.title, description=spec.description,
            order=spec.order, params_model=spec.params_model, run=explode,
        )
    )
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    stored = JobsRepo(conn).get(job.id)
    assert stored.status == "failed"
    assert "kaboom" in stored.error


def test_subscribers_receive_live_events(runner, conn):
    broker = runner.broker
    job = runner.submit("check_connection", {})
    queue_ = broker.subscribe(job.id)
    runner.wait_idle()
    broker.publish(job.id, {"type": "sentinel"})
    received = []
    while not queue_.empty():
        received.append(queue_.get_nowait())
    assert any(item.get("type") == "sentinel" for item in received)


def test_jobs_run_one_at_a_time(runner, conn):
    first = runner.submit("check_connection", {})
    second = runner.submit("check_connection", {})
    runner.wait_idle()
    repo = JobsRepo(conn)
    assert repo.get(first.id).status == "done"
    assert repo.get(second.id).status == "done"

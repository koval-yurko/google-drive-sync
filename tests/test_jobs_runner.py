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


def test_wait_idle_waits_for_every_rapidly_submitted_job(runner, conn):
    jobs = [runner.submit("check_connection", {}) for _ in range(5)]
    runner.wait_idle()
    repo = JobsRepo(conn)
    for job in jobs:
        assert repo.get(job.id).status == "done"


def test_stop_then_start_again_works(conn):
    """A fresh start() after a clean stop() must spawn a live worker again.

    Guards against stop() resetting self._thread to None while the old
    thread is still alive (or leaving it set after it already died): either
    bug would make this start()/submit() silently do nothing, and
    wait_idle() would time out.
    """
    repo = JobsRepo(conn)
    broker = EventBroker()

    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=FakeDrive(), settings=SettingsRepo(conn),
            config=Config.load(),
        )

    r = JobRunner(context_factory=context_factory, repo=repo, broker=broker)
    r.start()
    r.stop()
    r.start()
    try:
        job = r.submit("check_connection", {})
        r.wait_idle()
        assert repo.get(job.id).status == "done"
    finally:
        r.stop()


def test_phase_and_item_counts_reach_the_job_row(runner, conn, monkeypatch):
    from photolib.actions import registry

    def phased(ctx, params):
        yield ProgressEvent("starting", progress=0.1, phase="Scan (1/2)",
                            done=1, total=4)
        yield ProgressEvent("finishing", progress=0.9, phase="Upload (2/2)",
                            done=4, total=4)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", phased)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    runner.wait_idle()
    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.phase == "Upload (2/2)"
    assert (reloaded.items_done, reloaded.items_total) == (4, 4)


def test_run_id_reaches_the_action(runner, conn, monkeypatch):
    from photolib.actions import registry

    seen = {}

    def peek(ctx, params):
        seen["run_id"] = ctx.run_id
        seen["cancellable"] = ctx.cancelled is not None
        yield ProgressEvent("ok", progress=1.0)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", peek)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert seen["run_id"] == JobsRepo(conn).get(job.id).run_id
    assert seen["cancellable"] is True


import threading


def test_cancelling_a_running_job_stops_it_and_keeps_checkpoints(
    runner, conn, monkeypatch
):
    from photolib.actions import registry
    from photolib.db.job_items_repo import JobItemsRepo

    started = threading.Event()
    closed = {}

    def slow(ctx, params):
        items = JobItemsRepo(ctx.conn)
        items.enumerate(ctx.run_id, "work", ["a", "b", "c"], "x")
        try:
            for key in ("a", "b", "c"):
                items.mark(ctx.run_id, "work", key, "done")
                started.set()
                yield ProgressEvent(key, progress=0.3)
        except GeneratorExit:
            closed["yes"] = True
            raise

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", slow)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    started.wait(timeout=5.0)
    runner.cancel(job.id)
    runner.wait_idle()

    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "cancelled"
    assert closed.get("yes") is True
    assert JobItemsRepo(conn).counts(reloaded.run_id, "work")["done"] >= 1


def test_cancelling_a_queued_job_never_starts_it(runner, conn, monkeypatch):
    from photolib.actions import registry

    ran = {}

    def never(ctx, params):
        ran["yes"] = True
        yield ProgressEvent("should not happen", progress=1.0)

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", never)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    runner.stop()
    job = runner.submit("check_connection", {})
    assert runner.cancel(job.id) is True
    runner.start()
    runner.wait_idle()
    assert "yes" not in ran
    assert JobsRepo(conn).get(job.id).status == "cancelled"


def test_cancelling_a_finished_job_is_false(runner, conn):
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert runner.cancel(job.id) is False

import threading

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


def test_submit_honours_a_run_id_declared_in_params(runner, conn):
    """A flow confirming the plan it just reported passes run_id in its own
    params, not as an explicit submit() argument — the route never threads
    one through. The job must continue that run, not mint a fresh one."""
    job = runner.submit("sync_archives", {"confirm": True, "run_id": "abc"})
    runner.wait_idle()
    assert job.run_id == "abc"
    assert JobsRepo(conn).get(job.id).run_id == "abc"


def test_an_explicit_run_id_argument_wins_over_params(runner, conn):
    """`/jobs/{id}/resume` passes both; its explicit value is authoritative."""
    job = runner.submit(
        "sync_archives", {"confirm": True, "run_id": "from-params"},
        run_id="from-argument",
    )
    runner.wait_idle()
    assert job.run_id == "from-argument"


def test_an_action_without_run_id_in_params_still_gets_one_generated(
    runner, conn
):
    """Regression guard: every existing action (no `run_id` field in its
    Params) must keep getting a fresh run_id, not None or a KeyError."""
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    assert job.run_id
    other = runner.submit("check_connection", {})
    runner.wait_idle()
    assert other.run_id != job.run_id


def test_cancelling_a_running_job_stops_it_and_keeps_checkpoints(
    runner, conn, monkeypatch
):
    from photolib.actions import registry
    from photolib.db.job_items_repo import JobItemsRepo

    started = threading.Event()
    may_proceed = threading.Event()
    closed = {}

    def slow(ctx, params):
        items = JobItemsRepo(ctx.conn)
        items.enumerate(ctx.run_id, "work", ["a", "b", "c"], "x")
        try:
            for key in ("a", "b", "c"):
                items.mark(ctx.run_id, "work", key, "done")
                started.set()
                yield ProgressEvent(key, progress=0.3)
                # Block until the test has issued the cancel, so the
                # runner's between-yields check cannot be outrun by a fast
                # generator racing ahead to mark_done before cancel() lands.
                assert may_proceed.wait(timeout=5.0), (
                    "test did not release the generator"
                )
        except GeneratorExit:
            closed["yes"] = True
            raise

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", slow)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    assert started.wait(timeout=5.0)
    runner.cancel(job.id)
    may_proceed.set()
    runner.wait_idle()

    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "cancelled"
    assert closed.get("yes") is True
    assert JobItemsRepo(conn).counts(reloaded.run_id, "work")["done"] >= 1


def test_a_generator_that_returns_on_cancellation_is_marked_cancelled_not_done(
    runner, conn, monkeypatch
):
    """Both flows notice cancellation themselves at an item boundary and
    `return` from their generator, rather than waiting for the runner to
    `close()` it (see sync_archives.py:74,116 and reorganize_library.py's
    several `_cancelled(ctx): return` checks). That ends the runner's `for`
    loop the same way an uncancelled finish does — normal exhaustion, no
    GeneratorExit — so a runner that only checked the cancel flag *inside*
    the loop (right after a yield) would never see it and would record the
    job `done`."""
    from photolib.actions import registry

    started = threading.Event()
    may_proceed = threading.Event()

    def self_cancelling(ctx, params):
        yield ProgressEvent("first", progress=0.3)
        started.set()
        # Block until the test has cancelled, so the check below cannot be
        # outrun by a fast generator racing ahead of cancel() landing.
        assert may_proceed.wait(timeout=5.0), (
            "test did not release the generator"
        )
        if ctx.cancelled is not None and ctx.cancelled.is_set():
            return
        yield ProgressEvent("second", progress=1.0)  # pragma: no cover

    spec = registry.get_action("check_connection")
    monkeypatch.setattr(spec, "run", self_cancelling)
    monkeypatch.setattr(registry, "get_action", lambda _id: spec)

    job = runner.submit("check_connection", {})
    assert started.wait(timeout=5.0)
    runner.cancel(job.id)
    may_proceed.set()
    runner.wait_idle()

    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "cancelled"


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


def test_cancel_of_a_done_job_never_moves_it_off_terminal_status(
    runner, conn, monkeypatch
):
    """cancel() must never move an already-terminal job off its terminal
    status, even when it is fooled into attempting to — e.g. by the stale
    status snapshot TOCTOU window described in JobRunner.cancel's docstring.
    This is the runner-level counterpart to
    test_mark_cancelled_is_a_guarded_noop_once_the_job_is_done: it drives
    the call through cancel() itself, all the way into the guarded UPDATE
    in JobsRepo.mark_cancelled.
    """
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    done = JobsRepo(conn).get(job.id)
    assert done.status == "done"

    # Force cancel() down its "queued" branch (which calls mark_cancelled
    # synchronously) for a job that has, in reality, already finished.
    stale_queued = done.model_copy(update={"status": "queued"})
    monkeypatch.setattr(runner._repo, "get", lambda _id: stale_queued)

    assert runner.cancel(job.id) is False

    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "done"
    assert reloaded.finished_at == done.finished_at


def test_cancelling_a_queued_job_never_emits_running_or_sets_started_at(
    runner, conn, monkeypatch
):
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
    subscription = runner.broker.subscribe(job.id)
    assert runner.cancel(job.id) is True
    runner.start()
    runner.wait_idle()

    assert "yes" not in ran
    reloaded = JobsRepo(conn).get(job.id)
    assert reloaded.status == "cancelled"
    assert reloaded.started_at is None

    statuses = []
    while not subscription.empty():
        payload = subscription.get_nowait()
        if payload.get("type") == "status":
            statuses.append(payload["status"])
    assert "running" not in statuses


def test_cancel_of_a_job_that_finishes_in_the_race_window_leaves_no_registry_entry(
    runner, conn, monkeypatch
):
    """Simulate the TOCTOU window: cancel() reads a stale 'running' snapshot
    for a job that has, in reality, already finished — so _execute's own
    `finally` has already popped its entry from the runner's cancel-event
    registry. cancel() must report False and must not resurrect that entry
    (it would otherwise leak for the life of the process, since _execute
    will never run again for this job_id to pop it).
    """
    job = runner.submit("check_connection", {})
    runner.wait_idle()
    finished = JobsRepo(conn).get(job.id)
    assert finished.status == "done"
    assert job.id not in runner._cancels  # _execute's own finally popped it

    stale_running = finished.model_copy(update={"status": "running"})
    monkeypatch.setattr(runner._repo, "get", lambda _id: stale_running)

    assert runner.cancel(job.id) is False
    assert job.id not in runner._cancels

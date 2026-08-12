import threading
import time

from photolib.db.jobs_repo import JobsRepo, _now
from photolib.db.settings_repo import SettingsRepo


def test_create_returns_queued_job(conn):
    job = JobsRepo(conn).create("check_connection", {"deep": True})
    assert job.status == "queued"
    assert job.action == "check_connection"
    assert job.params == {"deep": True}
    assert job.progress == 0.0
    assert job.id


def test_get_round_trips(conn):
    repo = JobsRepo(conn)
    created = repo.create("check_connection", {})
    assert repo.get(created.id) == created


def test_get_unknown_returns_none(conn):
    assert JobsRepo(conn).get("missing") is None


def test_lifecycle_transitions(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_running(job.id)
    assert repo.get(job.id).status == "running"
    assert repo.get(job.id).started_at is not None
    repo.mark_done(job.id)
    assert repo.get(job.id).status == "done"
    assert repo.get(job.id).progress == 1.0
    assert repo.get(job.id).finished_at is not None


def test_failure_records_error(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_failed(job.id, "boom")
    stored = repo.get(job.id)
    assert stored.status == "failed"
    assert stored.error == "boom"
    assert stored.finished_at is not None


def test_progress_updates(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "halfway")
    stored = repo.get(job.id)
    assert stored.progress == 0.5
    assert stored.message == "halfway"


def test_events_are_ordered_and_filterable(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.add_event(job.id, "info", "first")
    repo.add_event(job.id, "warn", "second")
    events = repo.events(job.id)
    assert [e.message for e in events] == ["first", "second"]
    assert [e.level for e in events] == ["info", "warn"]
    later = repo.events(job.id, after_id=events[0].id)
    assert [e.message for e in later] == ["second"]


def test_list_is_newest_first(conn):
    repo = JobsRepo(conn)
    first = repo.create("check_connection", {})
    second = repo.create("check_connection", {})
    ids = [j.id for j in repo.list()]
    assert ids.index(second.id) < ids.index(first.id)


def test_list_with_tied_timestamps(conn, monkeypatch):
    """Test that rowid DESC breaks ties when created_at timestamps are identical."""
    repo = JobsRepo(conn)
    fixed_time = "2026-08-10T12:00:00.000000+00:00"
    monkeypatch.setattr("photolib.db.jobs_repo._now", lambda: fixed_time)
    first = repo.create("check_connection", {})
    second = repo.create("check_connection", {})
    ids = [j.id for j in repo.list()]
    # Even with the same timestamp, second should come first due to rowid DESC
    assert ids.index(second.id) < ids.index(first.id)


def test_concurrent_mutations_are_safe(conn):
    """Test that concurrent writes to the same connection don't corrupt or lose data."""
    repo = JobsRepo(conn)
    job = repo.create("concurrent_test", {})
    errors = []
    event_count = [0]

    def add_events():
        try:
            for i in range(100):
                repo.add_event(job.id, "info", f"event_{i}")
                event_count[0] += 1
        except Exception as e:
            errors.append(f"add_events error: {e}")

    def update_progress():
        try:
            for i in range(100):
                repo.update_progress(job.id, i / 100, f"progress_{i}")
        except Exception as e:
            errors.append(f"update_progress error: {e}")

    t1 = threading.Thread(target=add_events)
    t2 = threading.Thread(target=update_progress)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrency errors: {errors}"
    events = repo.events(job.id)
    assert len(events) == 100, f"Expected 100 events, got {len(events)}"


def test_concurrent_reads_and_writes_are_safe(conn):
    """A read racing a write on the same connection must never corrupt data.

    test_concurrent_mutations_are_safe above races two already-locked
    writers against each other, which never touches the unlocked-read path.
    This test races get()/list()/events() (reads) against add_event()/
    update_progress() (writes) directly, which is the exact shape that
    produced sqlite3.InterfaceError: bad parameter or other API misuse
    before reads were guarded by the shared connection lock.
    """
    repo = JobsRepo(conn)
    job = repo.create("concurrent_test", {})
    errors = []

    def writer():
        try:
            for i in range(300):
                repo.add_event(job.id, "info", f"event_{i}")
                repo.update_progress(job.id, i / 300, f"progress_{i}")
        except Exception as e:
            errors.append(f"writer error: {e}")

    def reader():
        try:
            for _ in range(300):
                repo.get(job.id)
                repo.list()
                repo.events(job.id)
        except Exception as e:
            errors.append(f"reader error: {e}")

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrency errors: {errors}"


def test_concurrent_cross_repo_access_is_safe(conn):
    """Two different repo classes over the SAME connection must not race.

    JobsRepo and SettingsRepo are constructed over one shared
    sqlite3.Connection; each repo class must use the connection's shared
    lock (catalog.LockedConnection.lock), not a private per-instance lock,
    or a read in one repo class can run concurrently with a write in the
    other on the same connection. This is the live path: check_connection's
    run() calls ctx.settings.get_folder(...) from the worker thread while
    the main thread's submit() -> JobsRepo.create() runs concurrently.
    """
    jobs_repo = JobsRepo(conn)
    settings_repo = SettingsRepo(conn)
    job = jobs_repo.create("concurrent_test", {})
    errors = []

    def jobs_writer():
        try:
            for i in range(300):
                jobs_repo.add_event(job.id, "info", f"event_{i}")
        except Exception as e:
            errors.append(f"jobs_writer error: {e}")

    def settings_reader():
        try:
            for _ in range(300):
                settings_repo.get("photos_root")
        except Exception as e:
            errors.append(f"settings_reader error: {e}")

    t1 = threading.Thread(target=jobs_writer)
    t2 = threading.Thread(target=settings_reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrency errors: {errors}"


def test_create_generates_a_run_id(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    assert job.run_id
    assert repo.create("check_connection", {}).run_id != job.run_id


def test_create_accepts_an_explicit_run_id(conn):
    repo = JobsRepo(conn)
    first = repo.create("check_connection", {})
    second = repo.create("check_connection", {}, run_id=first.run_id,
                         resumed_from=first.id)
    assert second.run_id == first.run_id
    assert second.resumed_from == first.id


def test_mark_cancelled(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.mark_cancelled(job.id)
    reloaded = repo.get(job.id)
    assert reloaded.status == "cancelled"
    assert reloaded.finished_at


def test_update_progress_records_phase_and_counts(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "half", phase="Upload (5/5)",
                         done=12, total=40)
    reloaded = repo.get(job.id)
    assert (reloaded.phase, reloaded.items_done, reloaded.items_total) == (
        "Upload (5/5)", 12, 40
    )


def test_update_progress_leaves_phase_alone_when_not_supplied(conn):
    repo = JobsRepo(conn)
    job = repo.create("check_connection", {})
    repo.update_progress(job.id, 0.5, "half", phase="Scan (2/5)", done=3, total=9)
    repo.update_progress(job.id, 0.6, "more")
    reloaded = repo.get(job.id)
    assert (reloaded.phase, reloaded.items_done, reloaded.items_total) == (
        "Scan (2/5)", 3, 9
    )

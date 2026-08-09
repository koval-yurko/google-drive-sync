from photolib.db.jobs_repo import JobsRepo


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

import pytest

from photolib.db.job_items_repo import JobItemsRepo


def test_enumerate_creates_pending_rows(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    assert [r["item_key"] for r in repo.pending("run1", "upload")] == ["a", "b"]
    assert all(r["state"] == "pending" for r in repo.pending("run1", "upload"))


def test_enumerate_does_not_reset_finished_items(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    repo.mark("run1", "upload", "a", "done")
    repo.enumerate("run1", "upload", ["a", "b", "c"], "job2")
    assert [r["item_key"] for r in repo.pending("run1", "upload")] == ["b", "c"]


def test_pending_returns_failed_items_but_not_skipped(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b", "c"], "job1")
    repo.mark("run1", "upload", "a", "failed", {"why": "boom"})
    repo.mark("run1", "upload", "b", "skipped")
    keys = [r["item_key"] for r in repo.pending("run1", "upload")]
    assert keys == ["a", "c"]


def test_detail_round_trips_as_a_dict(conn):
    repo = JobItemsRepo(conn)
    repo.put("run1", "repack", "f1", "job1", "pending", {"to": "2025-01"})
    row = repo.pending("run1", "repack")[0]
    assert row["detail"] == {"to": "2025-01"}


def test_phases_and_runs_do_not_collide(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a"], "job1")
    repo.enumerate("run1", "repack", ["a"], "job1")
    repo.enumerate("run2", "upload", ["a"], "job1")
    repo.mark("run1", "upload", "a", "done")
    assert repo.pending("run1", "upload") == []
    assert len(repo.pending("run1", "repack")) == 1
    assert len(repo.pending("run2", "upload")) == 1


def test_counts_and_clear(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a", "b"], "job1")
    repo.mark("run1", "upload", "a", "done")
    assert repo.counts("run1", "upload") == {"pending": 1, "done": 1}
    repo.clear("run1", "upload")
    assert repo.counts("run1", "upload") == {}


def test_marking_an_item_failed_preserves_its_persisted_plan(conn):
    """The plan is the checkpoint: `put(..., "pending", plan)` writes what a
    resume must execute, and a later `mark(..., "failed", {"error": ...})`
    must not blow that away — it should fold the error onto the plan, not
    replace it."""
    repo = JobItemsRepo(conn)
    repo.put("run1", "dedupe", "f1", "job1", "pending", {"to": "2025-01"})
    repo.mark("run1", "dedupe", "f1", "failed", {"error": "boom"})
    row = repo.all("run1", "dedupe")[0]
    assert row["state"] == "failed"
    assert row["detail"] == {"to": "2025-01", "error": "boom"}


def test_marking_failed_with_no_prior_detail_just_sets_it(conn):
    repo = JobItemsRepo(conn)
    repo.enumerate("run1", "upload", ["a"], "job1")
    repo.mark("run1", "upload", "a", "failed", {"error": "boom"})
    row = repo.all("run1", "upload")[0]
    assert row["detail"] == {"error": "boom"}


def test_unknown_state_is_rejected(conn):
    repo = JobItemsRepo(conn)
    with pytest.raises(ValueError):
        repo.put("run1", "upload", "a", "job1", "wobbly")

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


def test_unknown_state_is_rejected(conn):
    repo = JobItemsRepo(conn)
    with pytest.raises(ValueError):
        repo.put("run1", "upload", "a", "job1", "wobbly")

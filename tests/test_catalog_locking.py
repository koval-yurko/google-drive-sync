"""The catalog connection is shared across threads, so it serialises itself."""

import threading

from photolib.db import catalog


def test_concurrent_writes_on_one_connection_do_not_interleave(tmp_path):
    """The connection is shared between the job runner and request threads.

    Without serialisation this raises or loses rows; with it, every insert
    lands exactly once.
    """
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE counter (n INTEGER)")

    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def hammer(base: int) -> None:
        barrier.wait()
        try:
            for i in range(50):
                conn.execute("INSERT INTO counter (n) VALUES (?)", (base + i,))
        except Exception as exc:  # noqa: BLE001 - the assertion is the report
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(b * 1000,)) for b in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert conn.execute("SELECT COUNT(*) FROM counter").fetchone()[0] == 200
    conn.close()


def test_execute_holds_the_connection_lock(tmp_path):
    """Pins the mechanism, not just the outcome: a statement must not run
    while another thread holds conn.lock."""
    conn = catalog.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (n INTEGER)")

    ran = threading.Event()

    def insert() -> None:
        conn.execute("INSERT INTO t (n) VALUES (1)")
        ran.set()

    with conn.lock:
        worker = threading.Thread(target=insert)
        worker.start()
        assert not ran.wait(timeout=0.2), "execute ran while the lock was held"

    worker.join(timeout=5.0)
    assert ran.is_set()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()

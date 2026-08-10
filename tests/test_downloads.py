"""The downloads folder: what is moving, and what an earlier run left."""

import threading
from datetime import datetime

from photolib.downloads import (
    InflightRegistry,
    observe,
    run_folder_name,
    sweep_empty,
)


def part(tmp_path, name: str, size: int):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def registered(tmp_path, *, on_disk: int, expected: int, uploaded: int = 0):
    registry = InflightRegistry()
    path = part(tmp_path, "IMG_1.HEIC.part", on_disk)
    registry.start(
        "e1", name="IMG_1.HEIC", destination="Photos/2023-11",
        expected_size=expected, path=path,
    )
    if uploaded:
        registry.uploaded("e1", uploaded)
    return registry


def test_a_started_transfer_appears_in_the_snapshot(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    [live] = registry.snapshot()
    assert live.name == "IMG_1.HEIC"
    assert live.destination == "Photos/2023-11"
    assert live.expected_size == 100
    assert live.uploaded == 0


def test_finishing_removes_the_transfer(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    registry.finish("e1")
    assert registry.snapshot() == []


def test_reporting_progress_for_an_unknown_key_is_harmless(tmp_path):
    registry = InflightRegistry()
    registry.uploaded("nobody", 500)          # must not raise
    assert registry.snapshot() == []


def test_a_partly_downloaded_file_reads_as_downloading(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    [view] = observe(registry.snapshot())
    assert view.phase == "downloading"
    assert view.bytes == 10
    assert view.total == 100


def test_a_fully_downloaded_file_reads_as_uploading(tmp_path):
    registry = registered(tmp_path, on_disk=100, expected=100, uploaded=40)
    [view] = observe(registry.snapshot())
    assert view.phase == "uploading"
    assert view.bytes == 40
    assert view.total == 100


def test_a_file_that_vanished_mid_poll_is_dropped(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    (tmp_path / "IMG_1.HEIC.part").unlink()
    assert observe(registry.snapshot()) == []


def test_concurrent_progress_reports_leave_a_consistent_snapshot(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=1000)

    def report(base: int) -> None:
        for offset in range(base, base + 100):
            registry.uploaded("e1", offset)

    threads = [threading.Thread(target=report, args=(n * 100,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    [live] = registry.snapshot()
    assert 0 <= live.uploaded < 800


def test_the_open_run_is_reported_and_then_cleared(tmp_path):
    registry = InflightRegistry()
    assert registry.run_dir is None
    registry.open_run(tmp_path / "2026-08-10_14-32-05")
    assert registry.run_dir == tmp_path / "2026-08-10_14-32-05"
    registry.close_run()
    assert registry.run_dir is None


def test_a_run_folder_is_named_for_its_start_time():
    assert run_folder_name(datetime(2026, 8, 10, 14, 32, 5)) == "2026-08-10_14-32-05"


def test_sweeping_deletes_empty_leftovers_and_keeps_full_ones(tmp_path):
    keep = tmp_path / "2026-08-10_14-32-05"
    keep.mkdir()
    (tmp_path / "2026-08-09_10-00-00").mkdir()
    full = tmp_path / "2026-08-09_22-14-01"
    full.mkdir()
    (full / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    stale = sweep_empty(tmp_path, keep=keep)

    assert not (tmp_path / "2026-08-09_10-00-00").exists()
    assert (full / "IMG_9.HEIC.part").exists()
    assert stale == [{"dir": "2026-08-09_22-14-01", "files": 1, "bytes": 2048}]


def test_sweeping_never_touches_the_folder_it_is_told_to_keep(tmp_path):
    keep = tmp_path / "2026-08-10_14-32-05"
    keep.mkdir()
    assert sweep_empty(tmp_path, keep=keep) == []
    assert keep.exists()

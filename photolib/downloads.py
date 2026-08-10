"""The downloads folder: what is moving through it, and what was left behind.

The registry is the only mutable state, and it is the only thing the worker
threads touch. Everything else is a `stat()` of a folder, so what the UI shows
is what is actually on disk.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Transfer:
    """One file being moved, as the mover sees it."""

    key: str
    name: str
    destination: str
    expected_size: int
    path: Path
    uploaded: int = 0


@dataclass(frozen=True)
class TransferView:
    """One file being moved, as a watcher sees it."""

    name: str
    phase: str          # 'downloading' | 'uploading'
    bytes: int
    total: int
    destination: str


class InflightRegistry:
    """Live transfers, keyed by entry id. Safe to call from worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, Transfer] = {}
        self._run_dir: Path | None = None

    def open_run(self, path: Path) -> None:
        with self._lock:
            self._run_dir = path

    def close_run(self) -> None:
        with self._lock:
            self._run_dir = None

    @property
    def run_dir(self) -> Path | None:
        with self._lock:
            return self._run_dir

    def start(
        self,
        key: str,
        *,
        name: str,
        destination: str,
        expected_size: int,
        path: Path,
    ) -> None:
        with self._lock:
            self._live[key] = Transfer(
                key=key, name=name, destination=destination,
                expected_size=expected_size, path=path,
            )

    def uploaded(self, key: str, offset: int) -> None:
        """Record upload progress. A key that has finished is not an error —
        the callback and the `finally` that clears it are on different clocks."""
        with self._lock:
            live = self._live.get(key)
            if live is not None:
                self._live[key] = replace(live, uploaded=offset)

    def finish(self, key: str) -> None:
        with self._lock:
            self._live.pop(key, None)

    def snapshot(self) -> list[Transfer]:
        with self._lock:
            return list(self._live.values())


def observe(transfers: list[Transfer]) -> list[TransferView]:
    """Merge the registry with the bytes actually on disk.

    A file whose `.part` has just been unlinked is dropped rather than raising:
    that race is the normal end of every transfer, not a fault.
    """
    views: list[TransferView] = []
    for live in transfers:
        try:
            on_disk = live.path.stat().st_size
        except OSError:
            continue
        downloading = on_disk < live.expected_size
        views.append(TransferView(
            name=live.name,
            phase="downloading" if downloading else "uploading",
            bytes=on_disk if downloading else live.uploaded,
            total=live.expected_size,
            destination=live.destination,
        ))
    return views


def run_folder_name(started: datetime) -> str:
    """Sortable, filesystem-safe, and readable at a glance in Finder."""
    return started.strftime("%Y-%m-%d_%H-%M-%S")


def stale_runs(root: Path, active: Path | None) -> list[dict]:
    """Report leftover run folders holding bytes. Deletes nothing."""
    if not root.is_dir():
        return []
    found: list[dict] = []
    for folder in sorted(root.iterdir()):
        if folder == active or not folder.is_dir():
            continue
        files = [f for f in folder.iterdir() if f.is_file()]
        if not files:
            continue
        found.append({
            "dir": folder.name,
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        })
    return found


def sweep_empty(root: Path, keep: Path) -> list[dict]:
    """Delete leftover run folders that hold nothing; report the ones that do.

    An empty folder carries no information, so removing it costs nothing. A
    folder holding bytes is the only evidence a crashed run leaves, so it is
    reported and left for its owner to delete.
    """
    if not root.is_dir():
        return []
    for folder in sorted(root.iterdir()):
        if folder == keep or not folder.is_dir():
            continue
        if not any(f.is_file() for f in folder.iterdir()):
            folder.rmdir()
    return stale_runs(root, active=keep)

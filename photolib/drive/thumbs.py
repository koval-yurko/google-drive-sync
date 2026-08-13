"""A disk cache in front of Drive's own thumbnail renderer.

Chrome cannot display HEIC and 591 of these files are HEIC, so the Library
cannot render the media directly. Drive already generates thumbnails for
everything it holds; this fetches them once and keeps the bytes, so scrolling
the grid a second time costs nothing.

Two sizes only. An open-ended size parameter arriving from a URL is an
unbounded cache keyed by whatever anyone asks for.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# 400 for the grid, 1600 for the lightbox.
SIZES: tuple[int, ...] = (400, 1600)

# Drive ids are URL-safe base64-ish. Anything else reaching here came from a
# crafted path, not from Drive.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ThumbnailUnavailable(Exception):
    """Drive has no thumbnail for this file — often just 'not yet'."""


class ThumbnailCache:
    def __init__(self, root: Path, drive) -> None:
        self._root = Path(root)
        self._drive = drive

    def path_for(self, drive_id: str, size: int) -> Path:
        if not _SAFE_ID.match(drive_id):
            raise ValueError(f"not a Drive file id: {drive_id!r}")
        if size not in SIZES:
            raise ValueError(f"size must be one of {SIZES}, not {size}")
        # Two levels of fanout: a flat directory of 1,284+ files is slow to
        # list and unpleasant to inspect by hand.
        return self._root / drive_id[:2] / f"{drive_id}-s{size}.jpg"

    def get(self, drive_id: str, size: int) -> bytes:
        """Cached bytes, fetching them from Drive on a miss."""
        path = self.path_for(drive_id, size)
        if path.exists():
            return path.read_bytes()

        content = self._drive.fetch_thumbnail(drive_id, size)
        if content is None:
            # Deliberately not cached. Drive generates thumbnails a little
            # after upload, so caching the absence would make a freshly
            # organised library permanently blank.
            raise ThumbnailUnavailable(
                f"Drive has not generated a thumbnail for {drive_id} yet"
            )

        self._write(path, content)
        return content

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        """Write atomically, so a truncated file is never served as whole."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, raw = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(content)
            os.replace(raw, path)
        except BaseException:
            Path(raw).unlink(missing_ok=True)
            raise

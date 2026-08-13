"""Pure naming rules for Google Photos Takeout exports.

No I/O, no Drive, no database — every function here is a string transformation,
which is why they are cheap to test exhaustively.

Takeout names a photo's metadata sidecar after the photo, with two quirks that
break naive matching:

1. A duplicate index sits *outside* the extension. `IMG_7324(1).PNG` may be
   described by `IMG_7324.PNG.supplemental-metadata(1).json`.
2. Sidecar filenames are truncated to 51 characters, which can cut the
   `.supplemental-metadata` marker part-way through.
"""

from __future__ import annotations

import os
import re

JSON_SUFFIX = ".json"
MARKER = ".supplemental-metadata"
TRUNCATION_LIMIT = 51

STILL_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}

_INDEX = re.compile(r"\((\d+)\)$")
_YEAR_FOLDER = re.compile(r"^Photos from (\d{4})$")


def is_truncated(sidecar_name: str) -> bool:
    """True when Takeout cut this sidecar name at its length limit."""
    return len(sidecar_name) >= TRUNCATION_LIMIT


def _strip_marker(base: str) -> str:
    """Remove a trailing `.supplemental-metadata`, including a truncated one."""
    dot = base.rfind(".")
    if dot < 0:
        return base
    tail = base[dot:]
    # A truncated marker is any leading fragment of the full marker, but must be
    # long enough not to swallow a real extension like `.HEIC`.
    if len(tail) >= 4 and MARKER.startswith(tail.lower()):
        return base[:dot]
    return base


def media_name_for_sidecar(sidecar_name: str) -> str:
    """Return the media filename a sidecar describes.

    Raises ValueError if the name is not a sidecar.
    """
    if not sidecar_name.lower().endswith(JSON_SUFFIX):
        raise ValueError(f"not a sidecar: {sidecar_name}")

    base = sidecar_name[: -len(JSON_SUFFIX)]

    index = ""
    match = _INDEX.search(base)
    if match:
        index = match.group(0)
        base = base[: match.start()]

    base = _strip_marker(base)

    if not index:
        return base

    root, ext = os.path.splitext(base)
    return f"{root}{index}{ext}"


def stem(name: str) -> str:
    """Filename without its final extension, duplicate index preserved."""
    return os.path.splitext(name)[0]


def year_from_path(path: str) -> int | None:
    """The year of a `Photos from YYYY` folder anywhere in the path."""
    for part in path.split("/"):
        match = _YEAR_FOLDER.match(part)
        if match:
            return int(match.group(1))
    return None


def is_live_photo_pair(a: str, b: str) -> bool:
    """True when two names are the still and video halves of one capture."""
    if stem(a).lower() != stem(b).lower():
        return False
    ext_a = os.path.splitext(a)[1].lower()
    ext_b = os.path.splitext(b)[1].lower()
    return (ext_a in STILL_EXTENSIONS and ext_b in VIDEO_EXTENSIONS) or (
        ext_b in STILL_EXTENSIONS and ext_a in VIDEO_EXTENSIONS
    )

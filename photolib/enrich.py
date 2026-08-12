"""What the catalog can learn about a file it did not upload.

Google gives Drive an EXIF capture time and, often, coordinates. Everything
else this app knows about a file arrived with the file, in a Takeout sidecar
that a manually-added file will never have. This module reads what Drive has
and nothing else — no filename guessing, no rules engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from photolib.drive.client import DriveFile

TAG_PREFIX = "t_"


@dataclass
class Enrichment:
    capture_hint: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None
    metadata_source: str = "none"
    """'exif' | 'file_time' | 'none' — which source supplied the date."""
    tag_slugs: list[str] = field(default_factory=list)


def enrichment_for(file: DriveFile, geocoder) -> Enrichment:
    """Everything Drive knows about `file`, resolved through `geocoder`."""
    coords = file.location()
    country = None
    if coords is not None and geocoder is not None and geocoder.enabled:
        country = geocoder.lookup(*coords)

    capture_hint, metadata_source = file.capture()

    return Enrichment(
        capture_hint=capture_hint,
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
        country=country,
        metadata_source=metadata_source,
        tag_slugs=sorted(
            key[len(TAG_PREFIX):]
            for key in (file.app_properties or {})
            if key.startswith(TAG_PREFIX)
        ),
    )

"""Bridge between Drive-hosted files and the ZIP reader."""

from __future__ import annotations

from photolib.ziparchive.reader import (
    RangeReader,
    ZipEntry,
    extract_entry,
    read_central_directory,
)

SIDECAR = "sidecar"
MEDIA = "media"


def drive_range_reader(client, file_id: str) -> RangeReader:
    """Adapt a Drive file into the RangeReader the ZIP reader expects."""

    def read_range(start: int, end: int) -> bytes:
        return client.read_range(file_id, start, end)

    return read_range


def list_archive_entries(client, file_id: str, size: int) -> list[ZipEntry]:
    return read_central_directory(drive_range_reader(client, file_id), size)


def extract_from_archive(client, file_id: str, entry: ZipEntry) -> bytes:
    return extract_entry(drive_range_reader(client, file_id), entry)


def classify(path: str) -> str:
    return SIDECAR if path.lower().endswith(".json") else MEDIA

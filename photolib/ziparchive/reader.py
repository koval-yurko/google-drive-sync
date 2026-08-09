"""Read ZIP archives over byte ranges, without downloading them whole.

A ZIP stores its index (the "central directory") at the end of the file, and
each entry's compressed bytes at a recorded offset. That lets us fetch a small
tail to learn what an archive contains, then fetch only the bytes of the one
entry we want.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from typing import Callable

RangeReader = Callable[[int, int], bytes]

EOCD_SIGNATURE = b"PK\x05\x06"
EOCD64_SIGNATURE = b"PK\x06\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
PROBE_SIZE = 1 << 20
STORED = 0
DEFLATED = 8


class CorruptEntryError(Exception):
    """Raised when extracted bytes do not match the recorded CRC32."""


class MalformedArchiveError(Exception):
    """Raised when the archive has no readable central directory."""


@dataclass
class ZipEntry:
    path: str
    name: str
    crc32: int
    size: int
    compressed_size: int
    method: int
    local_header_offset: int


def read_central_directory(read_range: RangeReader, total_size: int) -> list[ZipEntry]:
    """Return every non-directory entry recorded in the archive's index."""
    probe_len = min(total_size, PROBE_SIZE)
    tail = read_range(total_size - probe_len, total_size - 1)

    eocd = tail.rfind(EOCD_SIGNATURE)
    if eocd < 0:
        raise MalformedArchiveError("no end-of-central-directory record found")
    _, cd_size, cd_offset = struct.unpack("<HII", tail[eocd + 10 : eocd + 20])

    saturated = cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF
    eocd64 = tail.rfind(EOCD64_SIGNATURE)
    if saturated and eocd64 >= 0:
        _, cd_size, cd_offset = struct.unpack("<QQQ", tail[eocd64 + 32 : eocd64 + 56])

    directory = read_range(cd_offset, cd_offset + cd_size - 1)
    return _parse_central_directory(directory)


def _parse_central_directory(directory: bytes) -> list[ZipEntry]:
    entries: list[ZipEntry] = []
    pos = 0
    while pos + 46 <= len(directory) and directory[pos : pos + 4] == CENTRAL_SIGNATURE:
        (method,) = struct.unpack("<H", directory[pos + 10 : pos + 12])
        crc, comp_size, size, name_len, extra_len, comment_len = struct.unpack(
            "<IIIHHH", directory[pos + 16 : pos + 34]
        )
        (header_offset,) = struct.unpack("<I", directory[pos + 42 : pos + 46])
        path = directory[pos + 46 : pos + 46 + name_len].decode("utf-8", "replace")
        pos += 46 + name_len + extra_len + comment_len
        if path.endswith("/"):
            continue
        entries.append(
            ZipEntry(
                path=path,
                name=os.path.basename(path),
                crc32=crc,
                size=size,
                compressed_size=comp_size,
                method=method,
                local_header_offset=header_offset,
            )
        )
    return entries


def extract_entry(read_range: RangeReader, entry: ZipEntry) -> bytes:
    """Fetch and decompress one entry, verifying it against its CRC32."""
    header = read_range(entry.local_header_offset, entry.local_header_offset + 29)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = entry.local_header_offset + 30 + name_len + extra_len

    if entry.compressed_size == 0:
        raw = b""
    else:
        raw = read_range(start, start + entry.compressed_size - 1)

    content = zlib.decompress(raw, -15) if entry.method == DEFLATED else raw

    if zlib.crc32(content) != entry.crc32:
        raise CorruptEntryError(
            f"CRC mismatch for {entry.path}: "
            f"expected {entry.crc32}, got {zlib.crc32(content)}"
        )
    return content

"""Move one archived file into Drive, verified at both ends.

Takes a range reader and a writer; touches no database and starts no threads.
Every failure mode is therefore a plain unit test.

The order matters: bytes are inflated to a temp file and checked against the
CRC32 the ZIP index supplies **before** the upload starts, so corrupt bytes
never reach Drive. After the upload, Drive's own MD5 is compared against one
computed locally. Because the central directory gives the CRC away for free
and Drive returns an MD5 on upload, every byte is verified twice without a
single extra download.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import struct
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from photolib.drive.errors import RateLimitedError, TransientError
from photolib.drive.writer import CHUNK_SIZE, SessionExpiredError
from photolib.ziparchive.reader import DEFLATED, ZipEntry

MAX_CHUNK_ATTEMPTS = 6
BASE_DELAY = 0.5
MAX_DELAY = 30.0

# mimetypes reads the platform's mime database, which may or may not carry the
# formats an iPhone produces. Pin them so the answer does not depend on the
# machine the migration runs on.
_EXTRA_TYPES = {
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".mov": "video/quicktime",
}

RangeReader = Callable[[int, int], bytes]


@dataclass
class TransferResult:
    drive_file_id: str
    md5: str
    size: int


class TransferError(Exception):
    """A file did not move. `stage` says how far it got."""

    def __init__(self, reason: str, stage: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage      # 'read' | 'crc' | 'upload' | 'verify'


def mime_for(name: str) -> str:
    extension = os.path.splitext(name)[1].lower()
    if extension in _EXTRA_TYPES:
        return _EXTRA_TYPES[extension]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def spool_entry(
    read_range: RangeReader,
    entry: ZipEntry,
    dest: Path,
    chunk: int = CHUNK_SIZE,
) -> None:
    """Fetch and inflate one entry to `dest`, verifying it against its CRC32.

    Reads and inflates in slices, so peak memory is one chunk rather than one
    file. Raises TransferError(stage='crc') if the bytes do not match — at
    which point nothing has been sent anywhere.
    """
    header = read_range(entry.local_header_offset, entry.local_header_offset + 29)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = entry.local_header_offset + 30 + name_len + extra_len

    decompressor = zlib.decompressobj(-15) if entry.method == DEFLATED else None
    crc = 0
    written = 0

    with dest.open("wb") as out:
        offset = 0
        while offset < entry.compressed_size:
            end = min(offset + chunk, entry.compressed_size) - 1
            raw = read_range(start + offset, start + end)
            offset = end + 1
            data = decompressor.decompress(raw) if decompressor else raw
            if data:
                crc = zlib.crc32(data, crc)
                written += len(data)
                out.write(data)
        if decompressor:
            data = decompressor.flush()
            if data:
                crc = zlib.crc32(data, crc)
                written += len(data)
                out.write(data)

    if crc != entry.crc32:
        raise TransferError(
            f"CRC mismatch: expected {entry.crc32}, got {crc}", "crc"
        )
    if written != entry.size:
        raise TransferError(
            f"size mismatch: expected {entry.size} bytes, inflated {written}", "crc"
        )


def file_md5(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def upload_file(
    writer,
    source: Path,
    session_uri: str,
    total: int,
    offset: int = 0,
    on_progress: Callable[[int], None] | None = None,
):
    """Send `source` to an open session from `offset`, and return the file.

    A chunk that fails may have been partly committed, so recovery always
    re-asks Drive for the true offset rather than assuming.
    """
    attempts = 0
    with source.open("rb") as fh:
        while True:
            fh.seek(offset)
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                raise TransferError(
                    "upload consumed the whole file without Drive completing it",
                    "upload",
                )
            try:
                result = writer.send_chunk(session_uri, chunk, offset, total)
            except (RateLimitedError, TransientError) as exc:
                attempts += 1
                if attempts >= MAX_CHUNK_ATTEMPTS:
                    raise TransferError(
                        f"upload failed after {attempts} attempts: {exc}", "upload"
                    ) from exc
                time.sleep(min(BASE_DELAY * (2 ** attempts), MAX_DELAY))
                offset = writer.session_offset(session_uri, total)
                continue

            attempts = 0
            if result is not None:
                return result
            offset += len(chunk)
            if on_progress:
                on_progress(offset)


def transfer_entry(
    *,
    read_range: RangeReader,
    entry: ZipEntry,
    writer,
    parent_id: str,
    name: str,
    properties: dict[str, str],
    spool_dir: Path,
    session_uri: str | None = None,
    mime_type: str | None = None,
    on_session: Callable[[str], None] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> TransferResult:
    """Move one entry into `parent_id` as `name`. Raises TransferError."""
    spool_dir.mkdir(parents=True, exist_ok=True)
    handle, raw_path = tempfile.mkstemp(dir=spool_dir, suffix=".part")
    os.close(handle)
    spooled = Path(raw_path)

    try:
        spool_entry(read_range, entry, spooled)
        local_md5 = file_md5(spooled)

        offset = 0
        if session_uri:
            try:
                offset = writer.session_offset(session_uri, entry.size)
            except SessionExpiredError:
                session_uri = None
        if not session_uri:
            session_uri = writer.start_session(
                parent_id=parent_id,
                name=name,
                size=entry.size,
                mime_type=mime_type or mime_for(name),
                properties=properties,
            )
            offset = 0
            if on_session:
                on_session(session_uri)

        uploaded = upload_file(
            writer, spooled, session_uri, entry.size, offset, on_progress
        )

        if uploaded.md5 and uploaded.md5 != local_md5:
            # The object in Drive is provably wrong. Leaving it would mean a
            # corrupt file in the library and a duplicate on the next retry.
            writer.trash(uploaded.id)
            raise TransferError(
                f"MD5 mismatch: local {local_md5}, Drive {uploaded.md5}", "verify"
            )

        return TransferResult(
            drive_file_id=uploaded.id, md5=local_md5, size=entry.size
        )
    finally:
        spooled.unlink(missing_ok=True)

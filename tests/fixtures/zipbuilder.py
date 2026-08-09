"""Builds real ZIP archives in memory so the reader can be tested offline."""

from __future__ import annotations

import io
import zipfile


def build_zip(files: dict[str, bytes], compress: bool = True) -> bytes:
    """Produce ZIP bytes containing `files`, mapping archive path to content."""
    buf = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", method) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def build_zip64(files: dict[str, bytes]) -> bytes:
    """Produce a ZIP that carries ZIP64 end-of-central-directory records."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(zipfile.ZipInfo("forced-zip64.txt"), b"x")
        for path, content in files.items():
            zf.writestr(path, content)
    data = bytearray(buf.getvalue())
    return bytes(data)


def reader_for(data: bytes):
    """Return a RangeReader over an in-memory archive, with inclusive bounds."""
    def read_range(start: int, end: int) -> bytes:
        return bytes(data[start : end + 1])

    return read_range

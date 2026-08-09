"""Builds real ZIP archives in memory so the reader can be tested offline."""

from __future__ import annotations

import io
import struct
import zipfile


def build_zip(files: dict[str, bytes], compress: bool = True) -> bytes:
    """Produce ZIP bytes containing `files`, mapping archive path to content."""
    buf = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", method) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def build_zip_with_extra_and_comments(files: dict[str, bytes]) -> bytes:
    """Produce a ZIP with non-zero extra fields and comments in the central directory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            info = zipfile.ZipInfo(path)
            info.extra = b"EXTRA_DATA_HERE"
            info.comment = b"This is a comment"
            zf.writestr(info, content)
    return buf.getvalue()


def build_zip64(files: dict[str, bytes]) -> bytes:
    """Produce a ZIP with saturated 32-bit fields pointing to ZIP64 EOCD.

    Creates a normal ZIP, then overwrites the central directory offset and size
    with 0xFFFFFFFF and appends a ZIP64 EOCD record to force the reader to
    use the 64-bit values.
    """
    # First, create a normal ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)

    data = bytearray(buf.getvalue())

    # Find the End-of-Central-Directory record (EOCD signature: PK\x05\x06)
    eocd_sig = b"PK\x05\x06"
    eocd_pos = data.rfind(eocd_sig)

    if eocd_pos < 0:
        # Fallback: return original if no EOCD found
        return bytes(data)

    # Store original EOCD values for ZIP64 EOCD
    # At eocd_pos + 12: central directory size (4 bytes)
    # At eocd_pos + 16: central directory offset (4 bytes)
    orig_cd_size = struct.unpack("<I", data[eocd_pos + 12 : eocd_pos + 16])[0]
    orig_cd_offset = struct.unpack("<I", data[eocd_pos + 16 : eocd_pos + 20])[0]

    # Overwrite those fields with 0xFFFFFFFF to signal ZIP64
    data[eocd_pos + 12 : eocd_pos + 16] = struct.pack("<I", 0xFFFFFFFF)
    data[eocd_pos + 16 : eocd_pos + 20] = struct.pack("<I", 0xFFFFFFFF)

    # Now append ZIP64 EOCD and locator before the classic EOCD
    # ZIP64 EOCD record structure:
    # 0-3: signature PK\x06\x06
    # 4-11: size of ZIP64 EOCD record (minus sig and size field) = 44
    # 12-13: version made by
    # 14-15: version needed to extract
    # 16-19: disk number
    # 20-23: disk with central directory
    # 24-31: number of CD records on this disk
    # 32-39: total number of CD records
    # 40-47: size of central directory (8 bytes)
    # 48-55: offset of central directory start (8 bytes)

    zip64_eocd = bytearray()
    zip64_eocd.extend(b"PK\x06\x06")  # signature
    zip64_eocd.extend(struct.pack("<Q", 44))  # size of this record
    zip64_eocd.extend(struct.pack("<HH", 0x0314, 0x0314))  # version
    zip64_eocd.extend(struct.pack("<II", 0, 0))  # disk numbers
    zip64_eocd.extend(struct.pack("<QQ", 1, 1))  # CD record counts
    zip64_eocd.extend(struct.pack("<Q", orig_cd_size))  # CD size
    zip64_eocd.extend(struct.pack("<Q", orig_cd_offset))  # CD offset

    # ZIP64 EOCD Locator structure:
    # 0-3: signature PK\x06\x07
    # 4-7: disk number with ZIP64 EOCD
    # 8-15: offset of ZIP64 EOCD (relative to start)
    # 16-19: total number of disks

    zip64_locator = bytearray()
    zip64_locator.extend(b"PK\x06\x07")  # signature
    zip64_locator.extend(struct.pack("<I", 0))  # disk number
    zip64_locator.extend(struct.pack("<Q", eocd_pos + len(zip64_eocd)))  # ZIP64 EOCD offset
    zip64_locator.extend(struct.pack("<I", 1))  # total disks

    # Insert ZIP64 records before the classic EOCD
    data = data[:eocd_pos] + zip64_eocd + zip64_locator + data[eocd_pos:]

    return bytes(data)


def reader_for(data: bytes):
    """Return a RangeReader over an in-memory archive, with inclusive bounds."""
    def read_range(start: int, end: int) -> bytes:
        return bytes(data[start : end + 1])

    return read_range

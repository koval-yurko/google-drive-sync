import zlib

import pytest

from photolib.ziparchive.reader import (
    CorruptEntryError,
    extract_entry,
    read_central_directory,
)
from tests.fixtures.zipbuilder import build_zip, reader_for


def test_lists_all_entries():
    data = build_zip({"a/one.txt": b"hello", "a/two.txt": b"world"})
    entries = read_central_directory(reader_for(data), len(data))
    assert {e.path for e in entries} == {"a/one.txt", "a/two.txt"}


def test_entry_carries_name_size_and_crc():
    content = b"hello world" * 100
    data = build_zip({"deep/nested/photo.HEIC": content})
    (entry,) = read_central_directory(reader_for(data), len(data))
    assert entry.name == "photo.HEIC"
    assert entry.size == len(content)
    assert entry.crc32 == zlib.crc32(content)
    assert entry.method == 8


def test_extract_round_trips_deflated_content():
    content = b"the quick brown fox " * 500
    data = build_zip({"x.bin": content})
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    assert extract_entry(read_range, entry) == content


def test_extract_round_trips_stored_content():
    content = b"uncompressed payload"
    data = build_zip({"x.bin": content}, compress=False)
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    assert entry.method == 0
    assert extract_entry(read_range, entry) == content


def test_extract_rejects_corrupt_content():
    content = b"payload that will be damaged"
    data = build_zip({"x.bin": content})
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))
    tampered = entry.__class__(**{**entry.__dict__, "crc32": entry.crc32 ^ 0xFFFF})
    with pytest.raises(CorruptEntryError):
        extract_entry(read_range, tampered)


def test_handles_takeout_style_names():
    data = build_zip({
        "Takeout/Google Photos/Photos from 2022/IMG_9004.MOV": b"video",
        "Takeout/Google Photos/Photos from 2022/"
        "IMG_9004.MOV.supplemental-metadata.json": b"{}",
        "Takeout/Google Photos/Photos from 2026/IMG_7324(1).PNG": b"png",
    })
    entries = read_central_directory(reader_for(data), len(data))
    names = {e.name for e in entries}
    assert "IMG_9004.MOV" in names
    assert "IMG_7324(1).PNG" in names
    assert "IMG_9004.MOV.supplemental-metadata.json" in names


def test_directory_entries_are_skipped():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("folder/", b"")
        zf.writestr("folder/file.txt", b"data")
    data = buf.getvalue()
    entries = read_central_directory(reader_for(data), len(data))
    assert [e.path for e in entries] == ["folder/file.txt"]


def test_tail_smaller_than_probe_window():
    data = build_zip({"tiny.txt": b"a"})
    assert len(data) < 1 << 20
    entries = read_central_directory(reader_for(data), len(data))
    assert len(entries) == 1

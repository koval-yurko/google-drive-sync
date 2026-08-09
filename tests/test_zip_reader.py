import zlib

import pytest

from photolib.ziparchive.reader import (
    CorruptEntryError,
    extract_entry,
    read_central_directory,
)
from tests.fixtures.zipbuilder import (
    build_zip,
    build_zip64,
    build_zip_with_extra_and_comments,
    reader_for,
)


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


def test_zip64_eocd_code_path():
    """Verify ZIP64 EOCD record is used when 32-bit fields are saturated."""
    from tests.fixtures.zipbuilder import build_zip64

    data = build_zip64({
        "file1.txt": b"first file content",
        "file2.txt": b"second file content",
        "file3.txt": b"third file content",
    })
    read_range = reader_for(data)
    entries = read_central_directory(read_range, len(data))

    # Verify all files are correctly read via ZIP64 path
    assert len(entries) == 3
    paths = {e.path for e in entries}
    assert "file1.txt" in paths
    assert "file2.txt" in paths
    assert "file3.txt" in paths

    # Verify CRC and sizes are correctly parsed from ZIP64 EOCD
    for entry in entries:
        assert entry.size > 0
        assert entry.crc32 == zlib.crc32(b"first file content" if "file1" in entry.path else
                                          b"second file content" if "file2" in entry.path else
                                          b"third file content")


def test_central_directory_with_extra_fields_and_comments():
    """Verify central directory parsing advances correctly with non-zero extra and comment fields."""
    from tests.fixtures.zipbuilder import build_zip_with_extra_and_comments

    files = {
        "alpha.txt": b"content alpha",
        "beta.txt": b"content beta",
        "gamma.txt": b"content gamma",
    }
    data = build_zip_with_extra_and_comments(files)
    read_range = reader_for(data)
    entries = read_central_directory(read_range, len(data))

    # All three files must be found despite extra fields and comments
    assert len(entries) == 3

    # Verify each entry is correct
    entries_by_name = {e.name: e for e in entries}
    assert "alpha.txt" in entries_by_name
    assert "beta.txt" in entries_by_name
    assert "gamma.txt" in entries_by_name

    # Verify CRC values match original content
    assert entries_by_name["alpha.txt"].crc32 == zlib.crc32(b"content alpha")
    assert entries_by_name["beta.txt"].crc32 == zlib.crc32(b"content beta")
    assert entries_by_name["gamma.txt"].crc32 == zlib.crc32(b"content gamma")

    # Verify sizes are correct
    assert entries_by_name["alpha.txt"].size == len(b"content alpha")
    assert entries_by_name["beta.txt"].size == len(b"content beta")
    assert entries_by_name["gamma.txt"].size == len(b"content gamma")


def test_non_ascii_filenames():
    """Verify non-ASCII UTF-8 filenames (multi-byte, Cyrillic, emoji) are preserved."""
    data = build_zip({
        "Zdjęcia/wakacje-Gdańsk.HEIC": b"photo1",
        "Фотографии/отпуск.JPG": b"photo2",
        "🎯/target🎉.PNG": b"photo3",
    })
    read_range = reader_for(data)
    entries = read_central_directory(read_range, len(data))

    assert len(entries) == 3

    # Check that paths are correctly decoded
    paths = {e.path for e in entries}
    assert "Zdjęcia/wakacje-Gdańsk.HEIC" in paths
    assert "Фотографии/отпуск.JPG" in paths
    assert "🎯/target🎉.PNG" in paths

    # Check that names (basenames) are correctly extracted
    names = {e.name for e in entries}
    assert "wakacje-Gdańsk.HEIC" in names
    assert "отпуск.JPG" in names
    assert "target🎉.PNG" in names

    # Verify each can be extracted correctly
    for entry in entries:
        if "Zdjęcia" in entry.path:
            assert extract_entry(read_range, entry) == b"photo1"
        elif "Фотографии" in entry.path:
            assert extract_entry(read_range, entry) == b"photo2"
        elif "🎯" in entry.path:
            assert extract_entry(read_range, entry) == b"photo3"


def test_extract_empty_file():
    """Verify extraction of zero-byte (empty) files works and passes CRC verification."""
    content = b""
    # Use stored (uncompressed) method for empty file to ensure compressed_size == 0
    data = build_zip({"empty.txt": content}, compress=False)
    read_range = reader_for(data)
    (entry,) = read_central_directory(read_range, len(data))

    assert entry.size == 0
    assert entry.compressed_size == 0
    assert entry.crc32 == zlib.crc32(content)  # CRC of empty bytes
    assert entry.method == 0  # Stored, not deflated

    # Verify extraction returns empty bytes and passes CRC check
    extracted = extract_entry(read_range, entry)
    assert extracted == b""
    assert zlib.crc32(extracted) == entry.crc32

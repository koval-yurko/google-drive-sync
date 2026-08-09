import pytest

from photolib import archives
from photolib.drive.errors import DriveError, NotFoundError
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

CONTENTS = {
    "Takeout/Google Photos/Photos from 2022/IMG_9004.MOV": b"movie-bytes" * 50,
    "Takeout/Google Photos/Photos from 2022/"
    "IMG_9004.MOV.supplemental-metadata.json": b'{"title": "IMG_9004.MOV"}',
}


def drive_with_archive() -> tuple[FakeDrive, int]:
    data = build_zip(CONTENTS)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", data, parent="zips")
    return drive, len(data)


def test_lists_entries_from_a_drive_hosted_archive():
    drive, size = drive_with_archive()
    entries = archives.list_archive_entries(drive, "z1", size)
    assert {e.name for e in entries} == {
        "IMG_9004.MOV", "IMG_9004.MOV.supplemental-metadata.json",
    }


def test_extracts_a_single_entry_without_reading_whole_archive():
    drive, size = drive_with_archive()
    entries = archives.list_archive_entries(drive, "z1", size)
    media = next(e for e in entries if e.name == "IMG_9004.MOV")
    assert archives.extract_from_archive(drive, "z1", media) == CONTENTS[media.path]


def test_classify_distinguishes_sidecars_from_media():
    assert archives.classify("a/b/IMG_1.HEIC") == "media"
    assert archives.classify("a/b/IMG_1.MOV") == "media"
    assert archives.classify(
        "a/b/IMG_1.MOV.supplemental-metadata.json"
    ) == "sidecar"
    assert archives.classify("a/b/Anything.JSON") == "sidecar"


# --- Finding 1: Tests for get_file and list_children ---


def test_get_file_returns_drive_file_with_expected_attributes():
    drive = FakeDrive()
    drive.add_folder("parent", "parent-folder")
    file = drive.add_file("f1", "test.txt", b"content", parent="parent")

    result = drive.get_file("f1")
    assert result.id == "f1"
    assert result.name == "test.txt"
    assert result.size == 7
    assert result.md5 == file.md5
    assert result.parents == ["parent"]
    assert result.is_folder is False


def test_get_file_folder_has_is_folder_true():
    drive = FakeDrive()
    folder = drive.add_folder("folder1", "my-folder")

    result = drive.get_file("folder1")
    assert result.is_folder is True
    assert result.size is None or result.size == 0


def test_get_file_raises_not_found_for_unknown_id():
    drive = FakeDrive()

    with pytest.raises(NotFoundError, match="no such file"):
        drive.get_file("unknown")


def test_list_children_returns_only_direct_children():
    drive = FakeDrive()
    drive.add_folder("root", "root-folder")
    drive.add_folder("child1", "child-folder", parent="root")
    drive.add_file("file1", "file.txt", b"data", parent="root")
    # Grandchild should not be returned
    drive.add_folder("grandchild", "grandchild-folder", parent="child1")

    children = drive.list_children("root")
    assert len(children) == 2
    assert {c.id for c in children} == {"child1", "file1"}


def test_list_children_with_folders_only_filter():
    drive = FakeDrive()
    drive.add_folder("root", "root-folder")
    drive.add_folder("folder1", "folder", parent="root")
    drive.add_folder("folder2", "folder", parent="root")
    drive.add_file("file1", "file.txt", b"data", parent="root")

    children = drive.list_children("root", folders_only=True)
    assert len(children) == 2
    assert all(c.is_folder for c in children)
    assert {c.id for c in children} == {"folder1", "folder2"}


def test_list_children_sorts_folders_before_files_then_alphabetically():
    """Verified against live Drive API on 2026-08-10: orderBy=folder,name returns folders first."""
    drive = FakeDrive()
    drive.add_folder("root", "root-folder")
    # Add in random order
    drive.add_file("z_file", "zebra.txt", b"z", parent="root")
    drive.add_folder("alpha_folder", "alpha-dir", parent="root")
    drive.add_file("a_file", "alpha.txt", b"a", parent="root")
    drive.add_folder("z_folder", "zebra-dir", parent="root")
    drive.add_file("b_file", "beta.txt", b"b", parent="root")

    children = drive.list_children("root")
    names = [c.name for c in children]

    # All folders should come first, sorted by name
    # Then all files, sorted by name
    assert names == [
        "alpha-dir",
        "zebra-dir",
        "alpha.txt",
        "beta.txt",
        "zebra.txt",
    ]


# --- Finding 2: Tests for read_range error handling ---


def test_read_range_raises_not_found_for_unknown_file_id():
    drive = FakeDrive()
    drive.add_folder("folder", "folder", parent=None)
    drive.add_file("file1", "file.txt", b"content", parent="folder")

    with pytest.raises(NotFoundError, match="no such file"):
        drive.read_range("unknown_id", 0, 10)


def test_read_range_raises_drive_error_for_folder():
    """Folders cannot be downloaded; Drive returns fileNotDownloadable (non-404 error)."""
    drive = FakeDrive()
    folder = drive.add_folder("folder", "folder", parent=None)

    with pytest.raises(DriveError, match="file not downloadable"):
        drive.read_range("folder", 0, 10)

    # Verify it is NOT NotFoundError
    try:
        drive.read_range("folder", 0, 10)
    except Exception as exc:
        assert not isinstance(exc, NotFoundError)
        assert isinstance(exc, DriveError)


# --- Finding 4: Prove extraction doesn't read whole archive ---


def test_extracts_single_entry_reads_small_fraction_of_archive():
    """Prove selective extraction only reads necessary bytes, not the entire archive."""
    drive, size = drive_with_archive()

    # Clear any prior calls (from list_archive_entries)
    drive.range_calls.clear()

    entries = archives.list_archive_entries(drive, "z1", size)
    media = next(e for e in entries if e.name == "IMG_9004.MOV")

    # Clear calls from reading central directory, only track extraction
    drive.range_calls.clear()

    result = archives.extract_from_archive(drive, "z1", media)

    # Verify we got the correct content
    assert result == CONTENTS[media.path]

    # Calculate total bytes requested during extraction
    total_bytes_requested = sum(end - start + 1 for _, start, end in drive.range_calls)

    # The extraction should read far less than the entire archive
    # (well under 50% is a safe, non-brittle threshold)
    assert total_bytes_requested < size / 2, (
        f"Extraction read {total_bytes_requested} bytes "
        f"out of {size} total ({100 * total_bytes_requested / size:.1f}%) — "
        f"should be streaming, not downloading entire archive"
    )

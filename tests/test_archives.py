from photolib import archives
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

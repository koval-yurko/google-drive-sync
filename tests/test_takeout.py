import pytest

from photolib import takeout


@pytest.mark.parametrize(
    "sidecar, expected",
    [
        # the ordinary case
        ("IMG_9004.MOV.supplemental-metadata.json", "IMG_9004.MOV"),
        # the older, shorter form
        ("IMG_9004.MOV.json", "IMG_9004.MOV"),
        # the duplicate index sits OUTSIDE the extension, on both sides
        ("IMG_7324.PNG.supplemental-metadata(1).json", "IMG_7324(1).PNG"),
        ("IMG_7324.PNG(1).json", "IMG_7324(1).PNG"),
        # Takeout truncates the sidecar name at 51 characters, cutting the
        # "supplemental-metadata" marker part-way through
        ("IMG_1234.HEIC.supplemental-met.json", "IMG_1234.HEIC"),
        ("IMG_1234.HEIC.supp.json", "IMG_1234.HEIC"),
        # case is not normalised away
        ("photo.jpeg.supplemental-metadata.json", "photo.jpeg"),
    ],
)
def test_media_name_for_sidecar(sidecar, expected):
    assert takeout.media_name_for_sidecar(sidecar) == expected


def test_media_name_for_sidecar_rejects_non_sidecars():
    with pytest.raises(ValueError):
        takeout.media_name_for_sidecar("IMG_9004.MOV")


def test_is_truncated():
    assert takeout.is_truncated("a" * 47 + ".json") is True
    assert takeout.is_truncated("short.HEIC.json") is False


def test_stem_keeps_the_duplicate_index():
    assert takeout.stem("IMG_7324(1).PNG") == "IMG_7324(1)"
    assert takeout.stem("IMG_7324.PNG") == "IMG_7324"
    assert takeout.stem("no-extension") == "no-extension"


@pytest.mark.parametrize(
    "path, year",
    [
        ("Takeout/Google Photos/Photos from 2022/IMG_1.HEIC", 2022),
        ("Takeout/Google Photos/Photos from 2026/IMG_1.HEIC", 2026),
        ("Takeout/Google Photos/Lake Como/IMG_1.HEIC", None),
        ("Takeout/Google Photos/Photos from nineteen/IMG_1.HEIC", None),
    ],
)
def test_year_from_path(path, year):
    assert takeout.year_from_path(path) == year


def test_live_photo_pairs_are_recognised():
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_1.MOV") is True
    assert takeout.is_live_photo_pair("IMG_1.MOV", "IMG_1.HEIC") is True
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_2.MOV") is False
    assert takeout.is_live_photo_pair("IMG_1.HEIC", "IMG_1.JPG") is False

import pytest

from photolib.drive.thumbs import SIZES, ThumbnailCache, ThumbnailUnavailable
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("root", "Photos")
    fake.add_file("d1", "IMG_1.HEIC", b"heic-bytes", parent="root")
    fake.set_thumbnail("d1", b"jpeg")
    return fake


def test_sizes_are_the_two_the_ui_asks_for():
    assert SIZES == (400, 1600)


def test_a_miss_fetches_from_drive(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    assert cache.get("d1", 400) == b"jpeg-s400"
    assert drive.thumbnail_requests == [("d1", 400)]


def test_a_hit_does_not_touch_drive(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    drive.thumbnail_requests.clear()

    assert cache.get("d1", 400) == b"jpeg-s400"
    assert drive.thumbnail_requests == []


def test_each_size_is_cached_separately(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    assert cache.get("d1", 400) == b"jpeg-s400"
    assert cache.get("d1", 1600) == b"jpeg-s1600"


def test_the_bytes_land_on_disk(tmp_path, drive):
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    assert cache.path_for("d1", 400).read_bytes() == b"jpeg-s400"


def test_no_part_files_survive_a_successful_fetch(tmp_path, drive):
    """A half-written entry served as a whole one is the bug worth preventing."""
    cache = ThumbnailCache(tmp_path, drive)
    cache.get("d1", 400)
    assert list(tmp_path.glob("**/*.part")) == []


def test_a_file_drive_has_not_rendered_is_unavailable(tmp_path, drive):
    drive.add_file("d2", "IMG_2.HEIC", b"x", parent="root")   # no set_thumbnail
    cache = ThumbnailCache(tmp_path, drive)

    with pytest.raises(ThumbnailUnavailable):
        cache.get("d2", 400)


def test_an_unavailable_thumbnail_is_not_cached_as_empty(tmp_path, drive):
    """Otherwise a file uploaded a minute ago would never get a thumbnail."""
    drive.add_file("d2", "IMG_2.HEIC", b"x", parent="root")
    cache = ThumbnailCache(tmp_path, drive)
    with pytest.raises(ThumbnailUnavailable):
        cache.get("d2", 400)

    drive.set_thumbnail("d2", b"late")
    assert cache.get("d2", 400) == b"late-s400"


def test_an_unknown_size_is_refused(tmp_path, drive):
    with pytest.raises(ValueError):
        ThumbnailCache(tmp_path, drive).get("d1", 9999)


def test_a_drive_id_cannot_escape_the_cache_directory(tmp_path, drive):
    """Drive ids reach this from a URL path; treat them as hostile."""
    cache = ThumbnailCache(tmp_path, drive)
    with pytest.raises(ValueError):
        cache.path_for("../../etc/passwd", 400)


def test_the_cache_directory_is_created_on_demand(tmp_path, drive):
    cache = ThumbnailCache(tmp_path / "does" / "not" / "exist", drive)
    assert cache.get("d1", 400) == b"jpeg-s400"

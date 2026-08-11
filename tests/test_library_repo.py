import pytest

from photolib.db.library_repo import Filters, LibraryRepo


@pytest.fixture
def library(conn):
    """Two months, a video, a duplicate, and one file this tool never uploaded."""
    conn.execute("INSERT INTO archives (drive_id, name, size) VALUES ('a1', 'part-001.zip', 99)")
    files = [
        # drive_id, name, month, mime
        ("d1", "IMG_1.HEIC", "2025-05", "image/heic"),
        ("d2", "IMG_2.HEIC", "2025-05", "image/heic"),
        ("d3", "VID_1.MOV", "2025-06", "video/quicktime"),
        ("d4", "NOTES.txt", "2025-06", "text/plain"),
        ("orphan", "STRAY.JPG", "2025-06", "image/jpeg"),
    ]
    for drive_id, name, month, mime in files:
        conn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, mime_type) "
            "VALUES (?, ?, ?, 'md5', 100, ?)",
            (drive_id, name, month, mime),
        )
    # media rows for everything but 'orphan'
    rows = [
        # entry path, drive_file_id, capture, country, dup_of, dup_reason
        ("Takeout/1.HEIC", "d1", 1700000000, "Poland", None, None),
        ("Takeout/2.HEIC", "d2", 1700000100, "Portugal", "2025-05",
         "name and size match an existing file"),
        ("Takeout/3.MOV", "d3", 1710000000, "Poland", None, None),
        ("Takeout/4.txt", "d4", None, None, None, None),
    ]
    for index, (path, drive_id, capture, country, dup, reason) in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO entries (id, archive_id, path, name, crc32, size, "
            "  compressed_size, method, local_header_offset, kind) "
            "VALUES (?, 1, ?, ?, 0, 100, 50, 8, 0, 'media')",
            (index, path, path.rsplit("/", 1)[-1]),
        )
        conn.execute(
            "INSERT INTO media (entry_id, capture_time, capture_source, "
            "  country, duplicate_of, duplicate_reason, upload_status, drive_file_id) "
            "VALUES (?, ?, 'sidecar', ?, ?, ?, 'done', ?)",
            (index, capture, country, dup, reason, drive_id),
        )
    conn.commit()
    return LibraryRepo(conn)


def test_lists_everything_by_default(library):
    result = library.list_files(Filters(), limit=100, offset=0)
    assert result["total"] == 5
    assert {row["drive_id"] for row in result["rows"]} == {"d1", "d2", "d3", "d4", "orphan"}


def test_a_file_with_no_media_row_still_appears(library):
    """Anything dropped into Photos/ by other means must be browsable."""
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    orphan = next(row for row in rows if row["drive_id"] == "orphan")
    assert orphan["name"] == "STRAY.JPG"
    assert orphan["capture_time"] is None
    assert orphan["country"] is None
    assert orphan["month"] == "2025-06"


def test_rows_carry_the_source_archive(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    first = next(row for row in rows if row["drive_id"] == "d1")
    assert first["archive_name"] == "part-001.zip"


def test_filter_by_month(library):
    result = library.list_files(Filters(month="2025-05"), limit=100, offset=0)
    assert result["total"] == 2


def test_filter_by_country(library):
    result = library.list_files(Filters(country="Portugal"), limit=100, offset=0)
    assert result["total"] == 1


def test_filter_by_media_type(library):
    assert library.list_files(Filters(media_type="image"), 100, 0)["total"] == 3
    assert library.list_files(Filters(media_type="video"), 100, 0)["total"] == 1
    assert library.list_files(Filters(media_type="other"), 100, 0)["total"] == 1


def test_media_type_is_derived_on_every_row(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    by_id = {row["drive_id"]: row["media_type"] for row in rows}
    assert by_id == {
        "d1": "image", "d2": "image", "d3": "video",
        "d4": "other", "orphan": "image",
    }


def test_filter_by_duplicates(library):
    result = library.list_files(Filters(duplicates=True), limit=100, offset=0)
    assert result["total"] == 1
    assert result["rows"][0]["duplicate_reason"] == "name and size match an existing file"


def test_filter_by_search_is_case_insensitive(library):
    assert library.list_files(Filters(search="vid_"), 100, 0)["total"] == 1


def test_filter_by_tag(conn, library):
    conn.execute("INSERT INTO tags (name, slug, color) VALUES ('Family', 'family', '#f00')")
    conn.execute("INSERT INTO file_tags (drive_id, tag_id) VALUES ('d3', 1)")
    conn.commit()

    result = library.list_files(Filters(tag_id=1), limit=100, offset=0)
    assert [row["drive_id"] for row in result["rows"]] == ["d3"]


def test_filters_compose(library):
    result = library.list_files(
        Filters(month="2025-06", media_type="video"), limit=100, offset=0
    )
    assert [row["drive_id"] for row in result["rows"]] == ["d3"]


def test_trashed_files_are_never_listed(conn, library):
    conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    conn.commit()
    assert library.list_files(Filters(), 100, 0)["total"] == 4


def test_total_ignores_the_page_window(library):
    result = library.list_files(Filters(), limit=2, offset=0)
    assert result["total"] == 5
    assert len(result["rows"]) == 2


def test_offset_pages_without_repeating(library):
    first = library.list_files(Filters(), limit=2, offset=0)["rows"]
    second = library.list_files(Filters(), limit=2, offset=2)["rows"]
    assert not ({r["drive_id"] for r in first} & {r["drive_id"] for r in second})


def test_ordering_is_newest_month_first_then_name(library):
    rows = library.list_files(Filters(), limit=100, offset=0)["rows"]
    assert [row["drive_id"] for row in rows] == ["d4", "orphan", "d3", "d1", "d2"]


def test_all_ids_honours_the_filter_and_ignores_paging(library):
    """'Select all matching this filter' must reach past the rendered page."""
    assert library.all_ids(Filters(month="2025-05")) == ["d1", "d2"]


def test_facets_count_each_dimension(library):
    facets = library.facets()
    assert facets["total"] == 5
    assert facets["months"] == [
        {"value": "2025-06", "count": 3},
        {"value": "2025-05", "count": 2},
    ]
    assert {"value": "Poland", "count": 2} in facets["countries"]
    assert facets["types"] == [
        {"value": "image", "count": 3},
        {"value": "other", "count": 1},
        {"value": "video", "count": 1},
    ]
    assert facets["duplicates"] == 1


def test_detail_returns_one_file(library):
    detail = library.detail("d1")
    assert detail["name"] == "IMG_1.HEIC"
    assert detail["country"] == "Poland"
    assert detail["archive_name"] == "part-001.zip"


def test_detail_of_an_unknown_file_is_none(library):
    assert library.detail("nope") is None

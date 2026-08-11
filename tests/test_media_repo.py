import json

import pytest

from photolib.db.media_repo import MediaRepo


@pytest.fixture
def seeded(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.executemany(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES (1,?,?,1,10,5,8,0,?)",
        [
            ("d/IMG_1.HEIC", "IMG_1.HEIC", "media"),
            ("d/IMG_1.HEIC.json", "IMG_1.HEIC.json", "sidecar"),
            ("d/IMG_2.MOV", "IMG_2.MOV", "media"),
        ],
    )
    conn.commit()
    return conn


def test_save_sidecar_stores_parsed_and_raw(seeded):
    repo = MediaRepo(seeded)
    sid = repo.save_sidecar(
        2,
        {"title": "IMG_1.HEIC", "photo_taken_time": 1700000000,
         "latitude": 52.2, "longitude": 21.0},
        json.dumps({"title": "IMG_1.HEIC"}),
    )
    row = seeded.execute("SELECT * FROM sidecars WHERE id = ?", (sid,)).fetchone()
    assert row["title"] == "IMG_1.HEIC"
    assert row["photo_taken_time"] == 1700000000
    assert json.loads(row["raw_json"])["title"] == "IMG_1.HEIC"


def test_save_sidecar_is_idempotent(seeded):
    repo = MediaRepo(seeded)
    first = repo.save_sidecar(2, {"title": "a"}, "{}")
    again = repo.save_sidecar(2, {"title": "b"}, "{}")
    assert first == again
    row = seeded.execute("SELECT title FROM sidecars WHERE id = ?", (first,)).fetchone()
    assert row["title"] == "b"


def test_upsert_media_is_idempotent(seeded):
    repo = MediaRepo(seeded)
    assert repo.upsert_media(1) == repo.upsert_media(1)
    assert len(repo.all_media()) == 1


def test_media_defaults_to_pending(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    assert repo.all_media()[0]["upload_status"] == "pending"


def test_set_plan_writes_every_column(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(
        1, capture_time=1700000000, capture_source="photo_taken_time",
        latitude=52.2, longitude=21.0, country="Poland",
        target_folder="2023-11", target_name="IMG_1.HEIC",
        duplicate_of="back_2024_01", duplicate_reason="name and size match",
    )
    row = repo.all_media()[0]
    assert row["target_folder"] == "2023-11"
    assert row["country"] == "Poland"
    assert row["duplicate_reason"] == "name and size match"
    assert row["upload_status"] == "pending"   # a verdict never withholds upload


def test_clear_plan_keeps_upload_results(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    seeded.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'x' WHERE entry_id = 1"
    )
    seeded.commit()
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["target_folder"] is None
    assert row["upload_status"] == "done"
    assert row["drive_file_id"] == "x"


def test_all_media_joins_entry_and_archive(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    row = repo.all_media()[0]
    assert row["name"] == "IMG_1.HEIC"
    assert row["path"] == "d/IMG_1.HEIC"
    assert row["archive_name"] == "a.zip"


def test_summary_counts(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.upsert_media(3)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC",
                  capture_source="photo_taken_time")
    repo.set_plan(3, duplicate_of="back_2024_01", duplicate_reason="name match")
    s = repo.summary()
    assert s["media"] == 2
    assert s["planned"] == 1
    assert s["duplicates"] == 1
    assert s["unplanned"] == 1


def test_unpaired_sidecars(seeded):
    repo = MediaRepo(seeded)
    assert [r["name"] for r in repo.unpaired_sidecars()] == ["IMG_1.HEIC.json"]
    repo.save_sidecar(2, {"title": "x"}, "{}")
    repo.upsert_media(1)
    repo.link_sidecar(1, 1)
    assert repo.unpaired_sidecars() == []

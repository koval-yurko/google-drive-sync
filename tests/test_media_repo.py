import json

import pytest

from photolib.db.media_repo import MediaRepo


@pytest.fixture
def seeded_entry(conn):
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z1', 'a.zip', 10)"
    )
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind)"
        " VALUES (1, 'd/a.heic', 'a.heic', 1, 10, 5, 8, 0, 'media')"
    )
    conn.commit()
    entry_id = conn.execute("SELECT id FROM entries").fetchone()["id"]
    MediaRepo(conn).upsert_media(entry_id)
    return entry_id


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
    assert row["upload_status"] == "done"
    assert row["drive_file_id"] == "x"


def test_clear_plan_keeps_a_done_rows_target(seeded):
    """Once a row is `done`, target_folder/target_name are a record of where
    the file actually is (see mark_uploaded), not a plan — clearing the plan
    to let Plan re-run must not erase that, even momentarily. Regression
    guard for the Plan re-run bug: Plan calls clear_plan() then rebuilds
    every row's target from scratch, and used to blindly wipe this too."""
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    seeded.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'x' WHERE entry_id = 1"
    )
    seeded.commit()
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["target_folder"] == "2023-11"
    assert row["target_name"] == "IMG_1.HEIC"


def test_clear_plan_resets_other_plan_columns_for_a_done_row(seeded):
    """Only target_folder/target_name are protected — a done row's capture
    time, country, and verdict are still fair game for Plan to recompute."""
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(
        1, target_folder="2023-11", target_name="IMG_1.HEIC",
        capture_time=1700000000, country="Poland",
        plan_verdict="skip", plan_match="drive-1",
    )
    seeded.execute(
        "UPDATE media SET upload_status = 'done', drive_file_id = 'x' WHERE entry_id = 1"
    )
    seeded.commit()
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["target_folder"] == "2023-11"   # protected
    assert row["capture_time"] is None
    assert row["country"] is None
    assert row["plan_verdict"] is None
    assert row["plan_match"] is None


def test_clear_plan_still_resets_target_for_a_pending_row(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["upload_status"] == "pending"
    assert row["target_folder"] is None
    assert row["target_name"] is None


def test_set_plan_does_not_overwrite_target_for_a_done_row(seeded):
    """The other half of the same rule (see `_DONE_PROTECTS`): even a direct
    set_plan call — not just the clear-then-rebuild Plan does — must not
    move a done row's recorded location out from under it."""
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.mark_uploaded(1, "x", "abc", target_folder="2023-11", target_name="IMG_1.HEIC")
    repo.set_plan(1, target_folder="9999-99", target_name="renamed.heic")
    row = repo.all_media()[0]
    assert row["target_folder"] == "2023-11"
    assert row["target_name"] == "IMG_1.HEIC"


def test_set_plan_still_writes_target_for_a_pending_row(seeded):
    repo = MediaRepo(seeded)
    repo.upsert_media(1)
    repo.set_plan(1, target_folder="2023-11", target_name="IMG_1.HEIC")
    repo.set_plan(1, target_folder="2024-01", target_name="renamed.heic")
    row = repo.all_media()[0]
    assert row["target_folder"] == "2024-01"
    assert row["target_name"] == "renamed.heic"


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


def test_skipped_rows_are_not_offered_for_upload(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01", target_name="a.heic",
                  plan_verdict="skip", plan_match="drive-1")
    assert repo.pending_uploads() == []


def test_verify_rows_are_offered_with_the_match_md5(conn, seeded_entry):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size) "
        "VALUES ('drive-9', 'a.heic', '2025-01', 'abc123', 3)"
    )
    conn.commit()
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01",
                  target_name="a~0000ff.heic", plan_verdict="verify",
                  plan_match="drive-9")
    row = repo.pending_uploads()[0]
    assert row["plan_verdict"] == "verify"
    assert row["match_md5"] == "abc123"


def test_a_trashed_adopt_target_yields_no_match_md5(conn, seeded_entry):
    """I4: `pending_uploads` must not hand Organize an adopt target Dedupe
    has since trashed — Organize adopts on MD5 alone, so a live `match_md5`
    is what makes adoption safe. Without the `trashed_at IS NULL` guard on
    the join, a trashed file's row still joins in its (now-stale) MD5 and
    the row is marked done against bytes sitting in Drive's trash."""
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size, "
        "trashed_at) VALUES ('drive-9', 'a.heic', '2025-01', 'abc123', 3, "
        "'2026-08-12T00:00:00+00:00')"
    )
    conn.commit()
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01",
                  target_name="a~0000ff.heic", plan_verdict="verify",
                  plan_match="drive-9")
    row = repo.pending_uploads()[0]
    assert row["plan_verdict"] == "verify"
    assert row["match_md5"] is None


def test_summary_separates_skipped_from_pending(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, target_folder="2025-01", target_name="a.heic",
                  plan_verdict="skip", plan_match="drive-1")
    summary = repo.summary()
    assert summary["skipped"] == 1
    assert summary["pending"] == 0


def test_verified_by_crc_keys_on_crc_and_size(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.mark_uploaded(seeded_entry, "drive-7", "deadbeef")
    key = next(iter(repo.verified_by_crc()))
    assert isinstance(key, tuple) and len(key) == 2
    assert repo.verified_by_crc()[key]["drive_file_id"] == "drive-7"


def test_clear_plan_resets_the_verdict(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, plan_verdict="skip", plan_match="drive-1")
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["plan_verdict"] is None and row["plan_match"] is None


@pytest.mark.parametrize("verdict", ["skip", "verify", "upload"])
def test_set_plan_accepts_every_valid_verdict(conn, seeded_entry, verdict):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, plan_verdict=verdict)
    assert repo.all_media()[0]["plan_verdict"] == verdict


def test_set_plan_rejects_an_invalid_verdict(conn, seeded_entry):
    repo = MediaRepo(conn)
    with pytest.raises(ValueError):
        repo.set_plan(seeded_entry, plan_verdict="wobbly")


def test_set_plan_allows_a_null_verdict_and_clear_plan_still_resets_it(conn, seeded_entry):
    repo = MediaRepo(conn)
    repo.set_plan(seeded_entry, plan_verdict=None)   # unplanned rows have no verdict yet
    assert repo.all_media()[0]["plan_verdict"] is None
    repo.set_plan(seeded_entry, plan_verdict="skip", plan_match="drive-1")
    repo.clear_plan()
    row = repo.all_media()[0]
    assert row["plan_verdict"] is None and row["plan_match"] is None

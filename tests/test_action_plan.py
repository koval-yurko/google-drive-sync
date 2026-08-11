import json

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.pair_metadata import Params as PairParams
from photolib.actions.pair_metadata import run as pair
from photolib.actions.plan_organize import Params, resolve_capture, run
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

# 1700000000 == 2023-11-14 UTC
SIDECAR = json.dumps({
    "title": "IMG_1.HEIC",
    "photoTakenTime": {"timestamp": "1700000000"},
    "geoData": {"latitude": 52.23, "longitude": 21.01},
}).encode()

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC": b"heic",
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC.supplemental-metadata.json":
        SIDECAR,
    # no sidecar: falls back to the year folder
    "Takeout/Google Photos/Photos from 2019/IMG_2.MOV": b"mov",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(ARCHIVE), parent="zips")
    drive.add_folder("photos", "Photos")
    drive.add_folder("back", "back_2024_01", parent="photos")
    drive.add_file("d1", "IMG_1.HEIC", b"heic", parent="back")
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    list(scan(context, ScanParams()))
    list(pair(context, PairParams()))
    return context


def by_name(ctx) -> dict:
    return {row["name"]: row for row in MediaRepo(ctx.conn).all_media()}


def test_resolve_capture_prefers_the_sidecar():
    when, source = resolve_capture(
        {"path": "Takeout/Google Photos/Photos from 2019/x.HEIC"},
        {"photo_taken_time": 1700000000, "creation_time": 1},
        None,
    )
    assert (when, source) == (1700000000, "photo_taken_time")


def test_resolve_capture_falls_back_to_the_year_folder():
    when, source = resolve_capture(
        {"path": "Takeout/Google Photos/Photos from 2019/x.HEIC"}, None, None
    )
    assert source == "year_folder"
    assert when is not None


def test_resolve_capture_gives_up_cleanly():
    when, source = resolve_capture({"path": "Takeout/Google Photos/Album/x.HEIC"}, None, None)
    assert (when, source) == (None, "unknown")


def test_target_folder_is_the_capture_month(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["target_folder"] == "2023-11"


def test_year_folder_fallback_is_recorded(ctx):
    list(run(ctx, Params()))
    row = by_name(ctx)["IMG_2.MOV"]
    assert row["capture_source"] == "year_folder"
    assert row["target_folder"] == "2019-01"


def test_duplicates_are_recorded_but_still_pending(ctx):
    list(run(ctx, Params()))
    row = by_name(ctx)["IMG_1.HEIC"]
    assert row["duplicate_of"] == "back_2024_01"
    assert "name" in row["duplicate_reason"]
    assert row["upload_status"] == "pending"      # never withheld


def test_trashed_copies_no_longer_count_as_duplicates(ctx):
    ctx.conn.execute(
        "UPDATE drive_files SET trashed_at = '2026-08-11T00:00:00+00:00' "
        "WHERE drive_id = 'd1'"
    )
    ctx.conn.commit()
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["duplicate_of"] is None


def test_a_file_is_not_a_duplicate_of_its_own_upload(ctx):
    row = by_name(ctx)["IMG_2.MOV"]
    MediaRepo(ctx.conn).mark_uploaded(row["entry_id"], "up9", "beef")
    ScanRepo(ctx.conn).record_drive_file(
        drive_id="up9", name="IMG_2.MOV", parent_path="2019-01",
        md5="beef", size=3, mime_type="video/quicktime",
    )
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_2.MOV"]["duplicate_of"] is None


def test_no_media_is_ever_marked_skipped(ctx):
    list(run(ctx, Params()))
    statuses = {r["upload_status"] for r in MediaRepo(ctx.conn).all_media()}
    assert statuses == {"pending"}


def test_place_is_absent_without_an_api_key(ctx):
    list(run(ctx, Params()))
    assert by_name(ctx)["IMG_1.HEIC"]["place"] is None


def test_rerun_replaces_the_previous_plan(ctx):
    list(run(ctx, Params()))
    list(run(ctx, Params()))
    rows = MediaRepo(ctx.conn).all_media()
    assert len(rows) == 2
    assert all(r["target_name"] for r in rows)


def test_name_collisions_within_a_month_are_disambiguated(ctx):
    conn = ctx.conn
    conn.execute(
        "INSERT INTO archives (drive_id, name, size) VALUES ('z9', 'extra.zip', 1)"
    )
    # A second IMG_2.MOV in the same year folder. Neither copy has a sidecar, so
    # both resolve to 2019-01 and genuinely collide. (A second IMG_1.HEIC would
    # not: the original has a sidecar dating it to 2023-11 while the copy would
    # fall back to 2023-01, so the two would never share a folder.)
    conn.execute(
        "INSERT INTO entries (archive_id, path, name, crc32, size, compressed_size,"
        " method, local_header_offset, kind) VALUES "
        "((SELECT id FROM archives WHERE drive_id='z9'),"
        " 'Takeout/Google Photos/Photos from 2019/IMG_2.MOV','IMG_2.MOV',"
        " 999,10,5,8,0,'media')"
    )
    conn.commit()
    # Pair Metadata is what normally creates media rows; this entry bypasses it.
    MediaRepo(conn).upsert_media(
        conn.execute("SELECT id FROM entries WHERE crc32 = 999").fetchone()["id"]
    )
    list(run(ctx, Params()))
    targets = [
        r["target_name"] for r in MediaRepo(ctx.conn).all_media()
        if r["name"] == "IMG_2.MOV"
    ]
    assert len(targets) == 2
    assert len(set(targets)) == 2, "colliding targets must be disambiguated"

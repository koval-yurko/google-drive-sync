import json

import pytest

from photolib.actions.base import ActionContext
from photolib.actions.steps.pair_metadata import Params, parse_sidecar, run
from photolib.actions.steps.scan_archives import Params as ScanParams
from photolib.actions.steps.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

SIDECAR = json.dumps({
    "title": "IMG_1.HEIC",
    "photoTakenTime": {"timestamp": "1700000000"},
    "creationTime": {"timestamp": "1700000500"},
    "geoData": {"latitude": 52.23, "longitude": 21.01, "altitude": 100.0},
    "url": "https://photos.google.com/x",
    "googlePhotosOrigin": {"mobileUpload": {"deviceType": "IOS_PHONE"}},
}).encode()

# The media is in part 1, its sidecar in part 2 — the 88% case.
PART_1 = {"Takeout/Google Photos/Photos from 2023/IMG_1.HEIC": b"heic"}
PART_2 = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC.supplemental-metadata.json":
        SIDECAR,
    "Takeout/Google Photos/Photos from 2023/IMG_9.MOV.supplemental-metadata.json":
        b'{"title": "IMG_9.MOV"}',
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    conn = catalog.connect(cfg.db_path)
    settings = SettingsRepo(conn)
    drive = FakeDrive()
    drive.add_folder("zips", "zip-source")
    drive.add_file("z1", "takeout-001.zip", build_zip(PART_1), parent="zips")
    drive.add_file("z2", "takeout-002.zip", build_zip(PART_2), parent="zips")
    drive.add_folder("photos", "Photos")
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    context = ActionContext(conn=conn, drive=drive, settings=settings, config=cfg)
    list(scan(context, ScanParams()))
    return context


def test_parse_sidecar_flattens_google_shapes():
    parsed = parse_sidecar(json.loads(SIDECAR))
    assert parsed["title"] == "IMG_1.HEIC"
    assert parsed["photo_taken_time"] == 1700000000
    assert parsed["creation_time"] == 1700000500
    assert parsed["latitude"] == 52.23
    assert parsed["device"] == "IOS_PHONE"


def test_parse_sidecar_treats_zero_coordinates_as_absent():
    parsed = parse_sidecar({"title": "x", "geoData": {"latitude": 0.0, "longitude": 0.0}})
    assert parsed["latitude"] is None
    assert parsed["longitude"] is None


def test_parse_sidecar_survives_missing_fields():
    parsed = parse_sidecar({"title": "x"})
    assert parsed["title"] == "x"
    assert parsed["photo_taken_time"] is None


def test_pairs_across_archive_parts(ctx):
    list(run(ctx, Params()))
    (row,) = [m for m in MediaRepo(ctx.conn).all_media() if m["name"] == "IMG_1.HEIC"]
    assert row["sidecar_id"] is not None


def test_creates_a_media_row_for_every_media_entry(ctx):
    list(run(ctx, Params()))
    assert len(MediaRepo(ctx.conn).all_media()) == 1


def test_reports_sidecars_with_no_media(ctx):
    messages = [e.message for e in run(ctx, Params())]
    assert any("1 sidecar" in m and "no media" in m for m in messages)


def test_rerun_is_idempotent(ctx):
    list(run(ctx, Params()))
    list(run(ctx, Params()))
    assert len(MediaRepo(ctx.conn).all_media()) == 1
    # Both sidecars are stored, including the orphan describing IMG_9.MOV — its
    # parsed data is kept for diagnosis. Re-running must not duplicate either.
    assert ctx.conn.execute("SELECT COUNT(*) FROM sidecars").fetchone()[0] == 2

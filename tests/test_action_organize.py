import hashlib
import json
import re
import threading

import pytest

from photolib.actions import organize
from photolib.actions.base import ActionContext
from photolib.actions.organize import Params, run
from photolib.actions.pair_metadata import Params as PairParams
from photolib.actions.pair_metadata import run as pair
from photolib.actions.plan_organize import Params as PlanParams
from photolib.actions.plan_organize import run as plan
from photolib.actions.scan_archives import Params as ScanParams
from photolib.actions.scan_archives import run as scan
from photolib.config import Config
from photolib.db import catalog
from photolib.db.media_repo import MediaRepo
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from photolib.downloads import InflightRegistry, observe
from photolib.transfer import TransferError
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip

HEIC = b"pretend this is a photograph" * 100
MOV = b"pretend this is a video" * 200

SIDECAR = json.dumps({
    "title": "IMG_1.HEIC",
    "photoTakenTime": {"timestamp": "1700000000"},     # 2023-11-14 UTC
    "geoData": {"latitude": 52.23, "longitude": 21.01},
}).encode()

STAMP = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")

ARCHIVE = {
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC": HEIC,
    "Takeout/Google Photos/Photos from 2023/IMG_1.HEIC.supplemental-metadata.json":
        SIDECAR,
    "Takeout/Google Photos/Photos from 2019/IMG_2.MOV": MOV,
}


@pytest.fixture
def archive_content() -> dict:
    """Entry name -> raw bytes, mirroring the archive `ctx` was built from.

    The zip builder already holds these bytes; return them rather than
    re-inflating the archive `ctx` built.
    """
    return {name.rsplit("/", 1)[-1]: content for name, content in ARCHIVE.items()}


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
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-source"))
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    # The fake speaks both halves of the interface, so it is drive and writer.
    context = ActionContext(
        conn=conn, drive=drive, settings=settings, config=cfg, writer=drive
    )
    list(scan(context, ScanParams()))
    list(pair(context, PairParams()))
    list(plan(context, PlanParams()))
    return context


def uploaded_names(ctx) -> dict:
    """Every uploaded file, keyed by name, wherever it landed."""
    found = {}
    for folder in ctx.drive.list_children("photos", folders_only=True):
        for file in ctx.drive.list_children(folder.id):
            found[file.name] = (folder.name, file)
    return found


def test_every_planned_file_is_uploaded(ctx):
    list(run(ctx, Params()))
    found = uploaded_names(ctx)
    assert set(found) == {"IMG_1.HEIC", "IMG_2.MOV"}
    assert found["IMG_1.HEIC"][0] == "2019-01 - 2023-11"
    assert found["IMG_2.MOV"][0] == "2019-01 - 2023-11"


def test_uploaded_bytes_are_the_real_bytes(ctx):
    list(run(ctx, Params()))
    file = uploaded_names(ctx)["IMG_1.HEIC"][1]
    assert file.md5 == hashlib.md5(HEIC).hexdigest()


def test_the_catalog_records_the_drive_id_and_md5(ctx):
    list(run(ctx, Params()))
    rows = {r["name"]: r for r in MediaRepo(ctx.conn).all_media()}
    assert rows["IMG_1.HEIC"]["upload_status"] == "done"
    assert rows["IMG_1.HEIC"]["drive_file_id"]
    assert rows["IMG_1.HEIC"]["md5"] == hashlib.md5(HEIC).hexdigest()


def test_uploads_land_in_the_library_index_without_a_rescan(ctx):
    list(run(ctx, Params()))
    rows = {
        r["name"]: r
        for r in ctx.conn.execute("SELECT * FROM drive_files")
    }
    assert rows["IMG_1.HEIC"]["parent_path"] == "2019-01 - 2023-11"
    assert rows["IMG_1.HEIC"]["md5"] == hashlib.md5(HEIC).hexdigest()
    assert rows["IMG_1.HEIC"]["mime_type"] == "image/heic"
    assert rows["IMG_2.MOV"]["mime_type"] == "video/quicktime"


def test_metadata_rides_along_but_no_tags(ctx):
    list(run(ctx, Params()))
    file = uploaded_names(ctx)["IMG_1.HEIC"][1]
    props = ctx.drive.properties_of(file.id)
    assert props["source_archive"] == "takeout-001.zip"
    assert "source_crc" in props and "capture_time" in props
    assert not any(key.startswith("t_") for key in props)   # tags are Phase 4


def test_rerunning_uploads_nothing_twice(ctx):
    list(run(ctx, Params()))
    before = len(ctx.drive.trashed), len(uploaded_names(ctx))
    list(run(ctx, Params()))
    assert (len(ctx.drive.trashed), len(uploaded_names(ctx))) == before


def test_a_bucket_folder_is_created_once(ctx):
    list(run(ctx, Params()))
    list(run(ctx, Params()))
    names = [f.name for f in ctx.drive.list_children("photos", folders_only=True)]
    assert sorted(names) == ["2019-01 - 2023-11"]


def test_an_unplanned_catalog_is_refused(ctx):
    MediaRepo(ctx.conn).clear_plan()
    events = list(run(ctx, Params()))
    assert events[-1].level == "error"
    assert uploaded_names(ctx) == {}


def test_a_missing_writer_is_refused(ctx):
    ctx.writer = None
    events = list(run(ctx, Params()))
    assert events[-1].level == "error"


def test_a_failed_file_is_recorded_and_the_others_still_run(ctx, monkeypatch):
    from photolib import transfer

    real = transfer.transfer_entry

    def explode(**kwargs):
        if kwargs["name"] == "IMG_2.MOV":
            raise transfer.TransferError("CRC mismatch", "crc")
        return real(**kwargs)

    monkeypatch.setattr("photolib.actions.organize.transfer_entry", explode)
    list(run(ctx, Params()))
    rows = {r["name"]: r for r in MediaRepo(ctx.conn).all_media()}
    assert rows["IMG_2.MOV"]["upload_status"] == "error"
    assert "CRC mismatch" in rows["IMG_2.MOV"]["error"]
    assert rows["IMG_1.HEIC"]["upload_status"] == "done"


def test_errors_are_left_alone_unless_retry_is_asked_for(ctx):
    repo = MediaRepo(ctx.conn)
    # Look the entry up by name — entry ids depend on the order Scan happened
    # to walk the archive, which is not something a test should assume.
    entry_id = next(
        r["entry_id"] for r in repo.pending_uploads() if r["name"] == "IMG_1.HEIC"
    )
    repo.mark_failed(entry_id, "earlier failure")
    list(run(ctx, Params()))
    assert "IMG_1.HEIC" not in uploaded_names(ctx)
    list(run(ctx, Params(retry_errors=True)))
    assert "IMG_1.HEIC" in uploaded_names(ctx)


def test_limit_caps_a_cautious_first_run(ctx):
    list(run(ctx, Params(limit=1)))
    assert len(uploaded_names(ctx)) == 1


def test_progress_is_reported_in_bytes(ctx):
    """File counts misreport a run mixing 45 KB stills with 500 MB videos."""
    events = [e for e in run(ctx, Params()) if e.progress is not None]
    assert events[-1].progress == 1.0
    assert all(0.0 <= e.progress <= 1.0 for e in events)


def test_the_run_gets_its_own_stamped_folder(ctx):
    events = run(ctx, Params())
    next(events)                                   # the "Uploading N file(s)" event
    live = [p.name for p in ctx.config.downloads_dir.iterdir()]
    assert len(live) == 1
    assert STAMP.fullmatch(live[0])
    list(events)                                   # drain the run


def test_the_run_folder_is_removed(ctx):
    list(run(ctx, Params()))
    assert list(ctx.config.downloads_dir.iterdir()) == []


def test_an_empty_leftover_folder_is_pruned(ctx):
    orphan = ctx.config.downloads_dir / "2026-08-09_10-00-00"
    orphan.mkdir(parents=True)
    list(run(ctx, Params()))
    assert not orphan.exists()


def test_a_leftover_folder_holding_bytes_is_kept_and_reported(ctx):
    orphan = ctx.config.downloads_dir / "2026-08-09_22-14-01"
    orphan.mkdir(parents=True)
    (orphan / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    messages = [event.message for event in run(ctx, Params())]

    assert (orphan / "IMG_9.HEIC.part").exists()
    assert any("2026-08-09_22-14-01" in m for m in messages)


def test_an_unusable_downloads_folder_stops_the_run_before_any_upload(ctx):
    ctx.config.downloads_dir.parent.mkdir(parents=True, exist_ok=True)
    ctx.config.downloads_dir.write_bytes(b"not a folder")

    events = list(run(ctx, Params()))

    assert events[-1].level == "error"
    assert uploaded_names(ctx) == {}


def test_a_single_worker_works_too(ctx):
    list(run(ctx, Params(workers=1)))
    assert len(uploaded_names(ctx)) == 2


def test_cancelling_stops_new_uploads_from_starting(ctx, monkeypatch):
    """C4 regression: every row used to be queued into the pool before the
    first yield, so a cancellation requested mid-run still let every
    already-queued row upload — the runner thread was simply blocked until
    they all finished. `ctx.cancelled` must be checked before a worker
    starts moving bytes, not only in the reporting loop after the fact."""
    ctx.cancelled = threading.Event()
    real_transfer = organize.transfer_entry

    def spy(**kwargs):
        result = real_transfer(**kwargs)
        # Single worker: this runs to completion, and only then does the
        # pool's one worker thread become free to pick up the second row —
        # so setting the flag here lands before that row's `move()` starts,
        # deterministically, with no sleep needed.
        ctx.cancelled.set()
        return result

    monkeypatch.setattr("photolib.actions.organize.transfer_entry", spy)
    list(run(ctx, Params(workers=1)))

    found = uploaded_names(ctx)
    assert len(found) == 1

    rows = {r["name"]: r for r in MediaRepo(ctx.conn).all_media()}
    untouched = [r for r in rows.values() if r["name"] not in found]
    assert len(untouched) == 1
    # Nothing happened to it: still pending, no session, no attempts — a
    # plain re-run picks it up exactly as if this run had never started.
    assert untouched[0]["upload_status"] == "pending"
    assert untouched[0]["upload_session_uri"] is None


def test_a_live_transfer_is_visible_while_it_moves(ctx, monkeypatch):
    """The registry is read from another thread, so read it from this one."""
    registry = InflightRegistry()
    ctx.inflight = registry
    seen: list = []

    original = organize.transfer_entry

    def spy(**kwargs):
        on_spool = kwargs.pop("on_spool")

        def watch(path):
            on_spool(path)
            seen.extend(observe(registry.snapshot()))

        return original(on_spool=watch, **kwargs)

    monkeypatch.setattr(organize, "transfer_entry", spy)
    list(run(ctx, Params(workers=1)))

    assert {view.name for view in seen} == {"IMG_1.HEIC", "IMG_2.MOV"}
    assert {view.destination for view in seen} == {"Photos/2019-01 - 2023-11"}
    assert all(view.total > 0 for view in seen)


def test_the_registry_is_empty_when_the_run_ends(ctx):
    registry = InflightRegistry()
    ctx.inflight = registry
    list(run(ctx, Params()))
    assert registry.snapshot() == []
    assert registry.run_dir is None


def test_a_failed_transfer_leaves_no_ghost(ctx, monkeypatch):
    registry = InflightRegistry()
    ctx.inflight = registry

    def explode(**kwargs):
        kwargs["on_spool"](ctx.config.downloads_dir / "ghost.part")
        raise TransferError("no", "upload")

    monkeypatch.setattr(organize, "transfer_entry", explode)
    list(run(ctx, Params(workers=1)))

    assert registry.snapshot() == []


def test_skip_rows_are_never_uploaded(ctx):
    repo = MediaRepo(ctx.conn)
    for row in repo.all_media():
        repo.set_plan(
            row["entry_id"], plan_verdict="skip", plan_match="drive-existing"
        )
    events = list(run(ctx, Params()))
    assert any("Nothing to upload" in e.message for e in events)


def test_verify_row_matching_drive_is_marked_done_against_that_file(
    ctx, archive_content
):
    """The bytes came down to prove identity; none went up."""
    repo = MediaRepo(ctx.conn)
    rows = repo.all_media()
    row, others = rows[0], rows[1:]
    # Isolate the row under test — `sessions_started == 0` should mean
    # nothing at all opened a session, not just this particular row.
    for other in others:
        repo.set_plan(other["entry_id"], plan_verdict="skip", plan_match=None)

    twin_md5 = hashlib.md5(archive_content[row["name"]]).hexdigest()
    ctx.conn.execute(
        "INSERT INTO drive_files "
        "(drive_id, name, parent_path, md5, size, mime_type) "
        "VALUES ('drive-twin', ?, '2025-01', ?, ?, 'image/heic')",
        (row["name"], twin_md5, row["entry_size"]),
    )
    ctx.conn.commit()
    repo.set_plan(row["entry_id"], plan_verdict="verify", plan_match="drive-twin")

    list(run(ctx, Params()))

    after = next(r for r in repo.all_media() if r["entry_id"] == row["entry_id"])
    assert after["upload_status"] == "done"
    assert after["drive_file_id"] == "drive-twin"
    assert ctx.drive.sessions_started == 0

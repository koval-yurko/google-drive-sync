import threading

import pytest

from photolib.actions import reorganize_library
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def drive():
    fake = FakeDrive()
    fake.add_folder("photos", "Photos")
    fake.add_folder("f-a", "trip-a", parent="photos")
    fake.add_folder("f-b", "trip-b", parent="photos")
    fake.add_folder("f-empty", "empty-folder", parent="photos")
    # Two byte-identical files in different folders: dedupe must trash one.
    fake.add_file("d-dup1", "IMG_1.HEIC", b"same-bytes", parent="f-a")
    fake.add_file("d-dup2", "IMG_1.HEIC", b"same-bytes", parent="f-b")
    # A file whose appProperties carry a t_family tag for Enrich to import.
    fake.add_file(
        "d-with-props", "IMG_3.HEIC", b"unique-bytes", parent="f-a",
        app_properties={"t_family": "1"},
    )
    return fake


@pytest.fixture
def reorg_context(conn, drive, tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Photos"))
    config = Config(
        repo_root=tmp_path,
        db_path=tmp_path / "t.db",
        credentials_path=tmp_path / "c.json",
        token_path=tmp_path / "t.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )
    return ActionContext(
        conn=conn, drive=drive, settings=settings, config=config, writer=drive,
        run_id="run-reorg", cancelled=threading.Event(),
    )


def test_dry_run_changes_nothing_in_drive(reorg_context, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    assert drive.trashed == []
    assert drive.moves == []


def test_dry_run_persists_the_plan(reorg_context, conn):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    assert items.pending(reorg_context.run_id, "dedupe")
    assert items.pending(reorg_context.run_id, "repack")


def test_confirm_executes_exactly_the_persisted_plan(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    planned = {
        i["item_key"]
        for i in JobItemsRepo(conn).pending(reorg_context.run_id, "dedupe")
    }
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    # `drive.trashed` also picks up folders the later, unplanned Sweep phase
    # empties out — sweep is recomputed live every confirm run, not part of
    # the persisted dedupe plan. Restrict the check to files (the "d-"
    # fixture ids) so it verifies dedupe trashed exactly its planned files,
    # not that nothing else in the flow trashed anything at all.
    files_trashed = {d for d in drive.trashed if d.startswith("d-")}
    assert files_trashed == planned


def test_confirm_without_a_plan_refuses(reorg_context, drive):
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert events[-1].level == "error"
    assert drive.trashed == []


def test_resume_does_not_repeat_finished_items(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    first = items.pending(reorg_context.run_id, "dedupe")[0]
    items.mark(reorg_context.run_id, "dedupe", first["item_key"], "done")
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert first["item_key"] not in drive.trashed


def test_dedupe_runs_before_repack(reorg_context, conn):
    """Files about to be trashed must not reserve space in a bucket."""
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params()
    ))
    phases = [e.phase for e in events if e.phase]
    assert phases.index(next(p for p in phases if p.startswith("Dedupe"))) < \
           phases.index(next(p for p in phases if p.startswith("Repack")))


def test_enrich_brings_drive_tags_into_the_catalog(reorg_context, conn):
    """A t_* appProperty on a file with no local tag creates that tag."""
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    slugs = {
        r["slug"] for r in conn.execute("SELECT slug FROM tags")
    }
    assert "family" in slugs
    linked = conn.execute(
        "SELECT COUNT(*) FROM file_tags"
    ).fetchone()[0]
    assert linked >= 1


def test_enrich_never_removes_a_local_tag(reorg_context, conn):
    from photolib.db.tags_repo import TagsRepo

    repo = TagsRepo(conn)
    tag = repo.create("local-only")
    repo.add_files(tag["id"], ["d-with-props"])
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM file_tags WHERE tag_id = ?", (tag["id"],)
    ).fetchone()[0]
    assert remaining == 1


def test_cancellation_stops_between_items(reorg_context, conn, drive):
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    reorg_context.cancelled.set()
    list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert drive.trashed == []

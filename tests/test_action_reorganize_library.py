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


def _make_ctx(conn, drive, tmp_path, run_id) -> ActionContext:
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
        run_id=run_id,
    )


def test_a_stale_source_folder_does_not_abort_the_confirmed_run(conn, tmp_path):
    """Regression coverage for reviewer Finding A.

    A repack move whose persisted `from_path` no longer resolves — neither
    via the live folder tree nor via Drive's own record of the file's
    current parent, because the file itself is gone too — must degrade to
    one failed item, not a bare `KeyError` that kills the whole confirmed
    run and leaves every later item, including the entire Sweep phase,
    unprocessed. Modeled on `tests/test_action_reorganize_stale_parent.py`,
    the equivalent regression for the older `reorganize` action.
    """
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("f-real", "actual-folder", parent="photos")
    drive.add_file("a", "IMG_A.HEIC", b"a", parent="f-real")
    drive.add_file("b", "IMG_B.HEIC", b"b", parent="f-real")
    run_id = "run-stale"
    ctx = _make_ctx(conn, drive, tmp_path, run_id)

    # A persisted plan as if a prior dry run had written it: two ordinary
    # moves, and one ("z") whose folder and file both vanished from Drive
    # between planning and confirming — its from_path is absent from the
    # live folder tree, and the get_file fallback for it fails too.
    items = JobItemsRepo(conn)
    items.put(run_id, "index", "photos", run_id, "done", {"files": 2})
    for drive_id, name, from_path in (
        ("a", "IMG_A.HEIC", "actual-folder"),
        ("b", "IMG_B.HEIC", "actual-folder"),
        ("z", "IMG_Z.HEIC", "gone-folder"),
    ):
        items.put(run_id, "repack", drive_id, run_id, "pending", {
            "drive_id": drive_id, "name": name, "new_name": name,
            "from_path": from_path, "to_folder": "unknown-date",
        })

    events = list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))

    failed = JobItemsRepo(conn).all(run_id, "repack", "failed")
    done = JobItemsRepo(conn).all(run_id, "repack", "done")
    assert [row["item_key"] for row in failed] == ["z"]
    assert {row["item_key"] for row in done} == {"a", "b"}
    assert "f-real" not in drive.get_file("a").parents
    assert "f-real" not in drive.get_file("b").parents
    error_events = [e for e in events if e.level == "error"]
    assert len(error_events) == 1
    assert "IMG_Z.HEIC" in error_events[0].message
    # Sweep still ran (a and b vacated actual-folder, emptying it) — the
    # run did not die on z's failure.
    assert any(e.phase and e.phase.startswith("Sweep") for e in events)
    assert "f-real" in drive.trashed
    assert events[-1].level != "error"


def test_confirm_reaches_sweep_when_dedupe_and_repack_plan_nothing(
    conn, tmp_path
):
    """Regression coverage for reviewer Finding B.

    A library with nothing to dedupe and nothing to repack still writes no
    rows for either phase, so gating the confirm refusal on those two
    phases specifically would wrongly refuse the one run where Sweep is
    the only work left. The "index" item, which a completed dry run always
    writes, is the honest marker instead.
    """
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("f-empty", "empty-only", parent="photos")
    run_id = "run-empty-plan"
    ctx = _make_ctx(conn, drive, tmp_path, run_id)

    list(reorganize_library.run(ctx, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    assert items.all(run_id, "dedupe") == []
    assert items.all(run_id, "repack") == []
    assert items.all(run_id, "index")

    events = list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))

    assert not any(e.level == "error" for e in events)
    assert any(e.phase and e.phase.startswith("Sweep") for e in events)
    assert "f-empty" in drive.trashed


def test_cancellation_before_repack_creates_no_bucket_folder(
    conn, tmp_path, monkeypatch
):
    """Regression coverage for reviewer Finding C (minor).

    `ensure_folders` is a real Drive write. Cancellation landing between
    Dedupe finishing and Repack starting must be caught before that write,
    not only inside the per-move loop after it.
    """
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("misfiled", "misfiled", parent="photos")
    drive.add_file("only", "IMG_ONLY.HEIC", b"only", parent="misfiled")
    run_id = "run-cancel-boundary"
    ctx = _make_ctx(conn, drive, tmp_path, run_id)
    ctx.cancelled = threading.Event()

    list(reorganize_library.run(ctx, reorganize_library.Params()))
    assert JobItemsRepo(conn).pending(run_id, "repack")  # one move is planned

    real_pending = JobItemsRepo.pending

    def cancel_when_repack_is_fetched(self, run_id_arg, phase):
        if phase == "repack":
            ctx.cancelled.set()
        return real_pending(self, run_id_arg, phase)

    monkeypatch.setattr(JobItemsRepo, "pending", cancel_when_repack_is_fetched)

    before = {f.name for f in drive.list_children("photos", folders_only=True)}
    list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))
    after = {f.name for f in drive.list_children("photos", folders_only=True)}

    assert after == before  # no "unknown-date" bucket folder was created
    assert drive.moves == []

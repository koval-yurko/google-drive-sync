import threading

import pytest

from photolib.actions import reorganize_library
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from photolib.db.tags_repo import TagsRepo
from photolib.drive.errors import DriveError, NotFoundError
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


def test_a_failed_dedupe_item_resumes_from_its_preserved_plan(
    reorg_context, conn, drive
):
    """Regression coverage for C3: `items.mark(..., "failed", {"error": ...})`
    used to overwrite the persisted `dedupe.Removal` plan wholesale, so a
    resumed run's `dedupe.Removal(**row["detail"])` raised TypeError forever
    for that run_id. Simulate an item that failed on an earlier confirm
    attempt (exactly the state a real failure would leave), then confirm
    again and check it completes instead of blowing up."""
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    doomed = items.pending(reorg_context.run_id, "dedupe")[0]
    items.mark(
        reorg_context.run_id, "dedupe", doomed["item_key"], "failed",
        {"error": "simulated transient failure"},
    )
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert not any(e.level == "error" for e in events)
    assert doomed["item_key"] in drive.trashed
    done_keys = {
        r["item_key"] for r in items.all(reorg_context.run_id, "dedupe", "done")
    }
    assert doomed["item_key"] in done_keys


def test_a_failed_repack_item_resumes_from_its_preserved_plan(
    reorg_context, conn, drive
):
    """Same regression as above, for Repack's `repack.Move(**row["detail"])`."""
    list(reorganize_library.run(reorg_context, reorganize_library.Params()))
    items = JobItemsRepo(conn)
    doomed = items.pending(reorg_context.run_id, "repack")[0]
    items.mark(
        reorg_context.run_id, "repack", doomed["item_key"], "failed",
        {"error": "simulated transient failure"},
    )
    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params(confirm=True)
    ))
    assert not any(e.level == "error" for e in events)
    moved_ids = {drive_id for drive_id, _ in drive.moves}
    assert doomed["item_key"] in moved_ids
    done_keys = {
        r["item_key"] for r in items.all(reorg_context.run_id, "repack", "done")
    }
    assert doomed["item_key"] in done_keys


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


def test_enrich_isolates_a_drive_error_and_the_flow_keeps_going(
    reorg_context, conn, drive, monkeypatch
):
    """I1 + I8(a): Enrich now works from the `DriveFile`s the Index walk
    already holds, so a file trashed between Index and Enrich no longer
    needs a second Drive call and no longer fails this way. The remaining
    `get_file` fallback covers the rarer race it was actually guarding
    against: a row `unenriched()` finds that this run's own walk did not
    produce — e.g. another writer's `record_drive_file` landing between
    Index committing and this loop reading `drive_files`. Simulate that
    race directly, and confirm the per-item isolation still holds for it."""
    from photolib import scan as scan_module

    real_index_destination = scan_module.index_destination

    def index_then_inject(drv, cn, folder_id):
        walked = real_index_destination(drv, cn, folder_id)
        cn.execute(
            "INSERT INTO drive_files (drive_id, name, parent_path, indexed_at) "
            "VALUES ('ghost', 'ghost.jpg', 'f-a', 'now')"
        )
        cn.commit()
        return walked

    monkeypatch.setattr(
        "photolib.actions.reorganize_library.scan.index_destination",
        index_then_inject,
    )

    real_get_file = drive.get_file

    def flaky(file_id):
        if file_id == "ghost":
            raise NotFoundError(f"no such file: {file_id}")
        return real_get_file(file_id)

    drive.get_file = flaky

    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params()
    ))

    error_events = [e for e in events if e.level == "error"]
    assert any("ghost" in e.message for e in error_events)
    assert any(e.phase and e.phase.startswith("Dedupe") for e in events)
    assert any(e.phase and e.phase.startswith("Repack") for e in events)
    failed = JobItemsRepo(conn).all(reorg_context.run_id, "enrich", "failed")
    assert any(r["item_key"] == "ghost" for r in failed)


def test_enrich_uses_the_walked_files_and_makes_no_get_file_calls(
    reorg_context, conn, drive
):
    """I8(a): the whole point of carrying `DriveFile`s from Index to Enrich
    is that Enrich must not re-fetch data the walk already holds. Every
    pending file in this fixture's tree was just walked by Index, so a
    `get_file` that raises must never actually be called."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("Enrich must not call get_file for a walked file")

    drive.get_file = _boom

    events = list(reorganize_library.run(
        reorg_context, reorganize_library.Params()
    ))

    assert not any(e.level == "error" for e in events)
    # The t_family property on d-with-props was still read and imported,
    # proving enrichment actually ran off the walked snapshot.
    slugs = {r["slug"] for r in conn.execute("SELECT slug FROM tags")}
    assert "family" in slugs


def test_enrich_isolates_a_duplicate_tag_error_and_the_flow_keeps_going(
    conn, tmp_path
):
    """I1: a `t_*` appProperty whose suffix collides with an existing
    canonical slug (`t_Family` -> ensure("Family") -> create("Family") ->
    slug "family" already taken) must not fail the whole flow either."""
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_file(
        "d1", "IMG_1.HEIC", b"one", parent="photos",
        app_properties={"t_Family": "1"},
    )
    TagsRepo(conn).create("family")  # the canonical slug is already taken

    run_id = "run-dup-tag"
    ctx = _make_ctx(conn, drive, tmp_path, run_id)

    events = list(reorganize_library.run(ctx, reorganize_library.Params()))

    error_events = [e for e in events if e.level == "error"]
    assert any("d1" in e.message for e in error_events)
    assert any(e.phase and e.phase.startswith("Dedupe") for e in events)
    assert any(e.phase and e.phase.startswith("Repack") for e in events)
    failed = JobItemsRepo(conn).all(run_id, "enrich", "failed")
    assert any(r["item_key"] == "d1" for r in failed)


def test_sweep_isolates_a_drive_error_and_keeps_going(conn, tmp_path):
    """I2: `repack.apply_sweep` raising on one folder must not abort the run
    after Dedupe and Repack have already written to Drive — the same
    per-item treatment as Dedupe and Repack already get."""
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("empty-a", "empty-a", parent="photos")
    drive.add_folder("empty-b", "empty-b", parent="photos")
    run_id = "run-sweep-error"
    ctx = _make_ctx(conn, drive, tmp_path, run_id)
    # A confirmed run with no prior dry run needs the "a plan exists" sentinel
    # a dry run would otherwise have written.
    JobItemsRepo(conn).put(run_id, "index", "photos", run_id, "done", {})

    real_trash = drive.trash

    def flaky(file_id):
        if file_id == "empty-a":
            raise DriveError("boom")
        return real_trash(file_id)

    drive.trash = flaky

    events = list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))

    assert "empty-b" in drive.trashed
    assert "empty-a" not in drive.trashed
    error_events = [e for e in events if e.level == "error"]
    assert any("empty-a" in e.message for e in error_events)
    failed = JobItemsRepo(conn).all(run_id, "sweep", "failed")
    assert any(r["item_key"] == "empty-a" for r in failed)
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

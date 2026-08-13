"""Regression coverage for a stale `parent_path` during a confirmed repack.

`reorganize_library`'s Repack loop falls back to asking Drive for a file's
current parent when the persisted `from_path` doesn't match any folder found
under the root — the plan can go stale between the dry run that wrote it and
the confirm that executes it. That fallback must be resolved independently
per file, never cached by the nominal (stale) `from_path` string: two files
can share the same stale `from_path` while actually living in two different
real folders, and caching the first file's resolved parent for the second
would leave the second still parented in its real old folder after the
"move".

The other half of this regression — one file's fallback *failing* must not
abort every other move in the same confirmed run — lives in
`test_action_reorganize_library.py` as
`test_a_stale_source_folder_does_not_abort_the_confirmed_run`, which was
written against the flow directly and covers the identical scenario.

These are deliberately two separate fixtures: sharing a stale path between
the failing file and a succeeding one would let the caching bug accidentally
*mask* the abort bug (a cached hit means the failing file's own fallback is
never attempted at all), which is exactly what happened the first time this
was written and had to be split.
"""

from photolib.actions import reorganize_library
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.job_items_repo import JobItemsRepo
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive

RUN_ID = "run-stale-parent"


def _ctx(conn, drive, tmp_path) -> ActionContext:
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
        run_id=RUN_ID,
    )


def test_a_shared_stale_parent_path_resolves_independently_per_file(
    conn, tmp_path, monkeypatch
):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("f1", "actual_folder_one", parent="photos")
    drive.add_folder("f2", "actual_folder_two", parent="photos")
    drive.add_file("x", "IMG_X.HEIC", b"x", parent="f1")
    drive.add_file("y", "IMG_Y.HEIC", b"y", parent="f2")
    ctx = _ctx(conn, drive, tmp_path)

    # A persisted plan as if a prior dry run had written it. x and y share
    # one stale from_path, but live in two different real folders — the plan
    # only agrees they're both misfiled, not where they are now. Index is
    # marked done so the confirm does not re-walk Drive and refresh it.
    items = JobItemsRepo(conn)
    items.put(RUN_ID, "index", "photos", RUN_ID, "done", {"files": 2})
    for drive_id, name in (("x", "IMG_X.HEIC"), ("y", "IMG_Y.HEIC")):
        items.put(RUN_ID, "repack", drive_id, RUN_ID, "pending", {
            "drive_id": drive_id, "name": name, "new_name": name,
            "from_path": "stale_path", "to_folder": "2024-01",
        })

    list(reorganize_library.run(
        ctx, reorganize_library.Params(confirm=True)
    ))

    x_parents = drive.get_file("x").parents
    y_parents = drive.get_file("y").parents

    # Each ends up with exactly one parent — the new bucket folder — not
    # two, which is what caching the first file's resolved parent under
    # the second file's identical stale `from_path` would produce (the
    # second file's real old parent would never be found to remove).
    assert len(x_parents) == 1
    assert len(y_parents) == 1
    assert "f1" not in x_parents
    assert "f2" not in y_parents
    assert x_parents == y_parents

"""Regression coverage for a stale `parent_path` during a confirmed repack.

`reorganize.run` falls back to asking Drive for a file's current parent
when the catalog's `parent_path` doesn't match any folder found under the
root — the catalog can go stale between a scan and a repack. That fallback
must behave exactly like the rest of the per-move execution loop:

- one file's fallback failing must not abort every other move in the same
  confirmed run (it previously did, when the fallback was hoisted into the
  folder-preparation step that aborts the whole run on any DriveError);
- the fallback must be resolved independently per file, never cached by
  the nominal (stale) `parent_path` string — two files can share the same
  stale `parent_path` while actually living in two different real folders,
  and caching the first file's resolved parent for the second would leave
  the second still parented in its real old folder after the "move".

These are two separate scenarios, kept in two separate fixtures: sharing a
stale path between the failing file and a succeeding one would let the
caching bug accidentally *mask* the abort bug (a cached hit means the
failing file's own fallback is never attempted at all), which is exactly
what happened the first time this test was written and had to be split.

No fixture in `tests/test_action_reorganize.py` exercises either case:
every catalogued `parent_path` there matches a real folder, so
`move.from_path not in folder_ids` is never true.
"""

from photolib.actions import reorganize
from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive

JAN_2024 = 1704067200  # 2024-01-01T00:00:00Z


def _run(ctx, **params) -> list:
    return list(reorganize.run(ctx, reorganize.Params(**params)))


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
    )


def test_a_fallback_resolution_failure_does_not_abort_the_batch(conn, tmp_path):
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("f-real", "actual_folder", parent="photos")
    drive.add_file("a", "IMG_A.HEIC", b"a", parent="f-real")
    drive.add_file("b", "IMG_B.HEIC", b"b", parent="f-real")
    # "z" is catalogued with its own stale parent_path — shared with no
    # other row, so its fallback is genuinely attempted, not skipped via
    # some other row's cached resolution — and never added to `drive` at
    # all, so that attempt itself fails.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
        " mime_type, capture_hint) VALUES "
        "('a', 'IMG_A.HEIC', 'actual_folder', 'aaaaaa11', 1, 'image/heic', ?),"
        "('b', 'IMG_B.HEIC', 'actual_folder', 'bbbbbb22', 1, 'image/heic', ?),"
        "('z', 'IMG_Z.HEIC', 'gone_z', 'cccccc33', 1, 'image/heic', ?)",
        (JAN_2024, JAN_2024, JAN_2024),
    )
    conn.commit()
    ctx = _ctx(conn, drive, tmp_path)

    events = _run(ctx, confirm=True)
    text = " ".join(e.message for e in events)

    # The old bug: this fallback lived in the folder-preparation step, so
    # z's failure aborted before a or b were ever attempted.
    assert "Cannot prepare destination folders" not in text
    assert "Moved 2 file(s)" in text
    assert "1 failed" in text
    assert "IMG_Z.HEIC" in text
    assert "f-real" not in drive.get_file("a").parents
    assert "f-real" not in drive.get_file("b").parents


def test_a_shared_stale_parent_path_resolves_independently_per_file(
    conn, tmp_path
):
    drive = FakeDrive()
    drive.add_folder("photos", "Photos")
    drive.add_folder("f1", "actual_folder_one", parent="photos")
    drive.add_folder("f2", "actual_folder_two", parent="photos")
    drive.add_file("x", "IMG_X.HEIC", b"x", parent="f1")
    drive.add_file("y", "IMG_Y.HEIC", b"y", parent="f2")
    # x and y share one stale parent_path, but live in two different real
    # folders — the catalog only agrees they're both misfiled, not where.
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path, md5, size,"
        " mime_type, capture_hint) VALUES "
        "('x', 'IMG_X.HEIC', 'stale_path', 'aaaaaa11', 1, 'image/heic', ?),"
        "('y', 'IMG_Y.HEIC', 'stale_path', 'bbbbbb22', 1, 'image/heic', ?)",
        (JAN_2024, JAN_2024),
    )
    conn.commit()
    ctx = _ctx(conn, drive, tmp_path)

    _run(ctx, confirm=True)

    x_parents = drive.get_file("x").parents
    y_parents = drive.get_file("y").parents

    # Each ends up with exactly one parent — the new bucket folder — not
    # two, which is what caching the first file's resolved parent under
    # the second file's identical stale `parent_path` would produce (the
    # second file's real old parent would never be found to remove).
    assert len(x_parents) == 1
    assert len(y_parents) == 1
    assert "f1" not in x_parents
    assert "f2" not in y_parents
    assert x_parents == y_parents

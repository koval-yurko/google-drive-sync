"""Opt-in checks against the real Drive account: `uv run pytest -m live`.

Everything else in the suite runs against a fake. These two facts cannot be
faked honestly: that Drive renders a thumbnail for a file we uploaded, and
that setting an appProperty to null really deletes it — which is the whole
mechanism by which `sync_tags` removes a tag.

Paths resolve relative to the repo root. From a git worktree the credentials
live in the main checkout, so point at it:
`PHOTOLIB_HOME=/path/to/main/checkout uv run pytest -m live`.
"""

from __future__ import annotations

import pytest

from photolib.config import Config
from photolib.db import catalog
from photolib.db.settings_repo import PHOTOS_ROOT, SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient
from photolib.drive.writer import DriveWriter

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def drive():
    config = Config.load()
    if not config.credentials_path.exists():
        pytest.skip("no credentials.json; set PHOTOLIB_HOME to the main checkout")
    client = DriveClient(TokenProvider(config.credentials_path, config.token_path))
    yield client
    client.close()


@pytest.fixture(scope="module")
def a_real_photo(drive):
    """One file already in the destination. Skips if nothing is organised yet."""
    config = Config.load()
    conn = catalog.connect(config.db_path)
    photos_root = SettingsRepo(conn).get_folder(PHOTOS_ROOT)
    if photos_root is None:
        pytest.skip("photos_root is not configured")
    row = conn.execute(
        "SELECT drive_id FROM drive_files WHERE trashed_at IS NULL "
        "AND mime_type LIKE 'image/%' LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        pytest.skip("no organised images yet; run Scan and Organize first")
    return row["drive_id"]


def test_drive_renders_a_thumbnail_for_a_real_file(drive, a_real_photo):
    content = drive.fetch_thumbnail(a_real_photo, 400)
    assert content is not None, "Drive returned no thumbnailLink"
    # JPEG magic. If this is HTML we followed an error page, not an image.
    assert content[:2] == b"\xff\xd8"


def test_the_two_sizes_really_differ(drive, a_real_photo):
    small = drive.fetch_thumbnail(a_real_photo, 400)
    large = drive.fetch_thumbnail(a_real_photo, 1600)
    assert small is not None and large is not None
    assert len(large) > len(small)


def test_an_app_property_round_trips_and_can_be_deleted(drive, a_real_photo):
    """The mechanism sync_tags relies on to remove a tag."""
    writer = DriveWriter(drive)
    before = drive.app_properties(a_real_photo)
    assert "t_photolib_live_test" not in before

    writer.update_properties(a_real_photo, {"t_photolib_live_test": "1"})
    try:
        assert drive.app_properties(a_real_photo)["t_photolib_live_test"] == "1"
    finally:
        writer.update_properties(a_real_photo, {"t_photolib_live_test": None})

    after = drive.app_properties(a_real_photo)
    assert "t_photolib_live_test" not in after
    # Nothing Organize wrote may have been disturbed.
    assert {k: v for k, v in after.items() if not k.startswith("t_")} == {
        k: v for k, v in before.items() if not k.startswith("t_")
    }

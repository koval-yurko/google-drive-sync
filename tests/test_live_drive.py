"""Opt-in tests against the real Drive account.

Run with: uv run pytest -m live -v
These are excluded from the default suite by the addopts in pyproject.toml.
"""

from __future__ import annotations

import pytest

from photolib.ziparchive import source as archives
from photolib.config import Config
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient

ZIP_FOLDER_ID = "1y2pqVRWi92920usgc7Yy1qe-81hqUljO"

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    cfg = Config.load()
    if not cfg.token_path.exists():
        pytest.skip("token.json not present")
    drive = DriveClient(TokenProvider(cfg.credentials_path, cfg.token_path))
    yield drive
    drive.close()


def test_can_refresh_token_and_read_folder(client):
    folder = client.get_file(ZIP_FOLDER_ID)
    assert folder.is_folder


def test_zip_source_contains_seventeen_archives(client):
    children = client.list_children(ZIP_FOLDER_ID)
    zips = [c for c in children if c.name.lower().endswith(".zip")]
    assert len(zips) == 17


def test_reads_a_real_archive_index_over_ranges(client):
    zips = [
        c
        for c in client.list_children(ZIP_FOLDER_ID)
        if c.name.lower().endswith(".zip")
    ]
    first = sorted(zips, key=lambda f: f.name)[0]
    entries = archives.list_archive_entries(client, first.id, first.size)
    assert len(entries) > 100
    assert any(e.name.endswith(".supplemental-metadata.json") for e in entries)
    assert any(e.path.startswith("Takeout/Google Photos/") for e in entries)


def test_extracts_one_sidecar_and_verifies_its_crc(client):
    import json

    zips = [
        c
        for c in client.list_children(ZIP_FOLDER_ID)
        if c.name.lower().endswith(".zip")
    ]
    first = sorted(zips, key=lambda f: f.name)[0]
    entries = archives.list_archive_entries(client, first.id, first.size)
    sidecar = next(e for e in entries if e.path.lower().endswith(".json"))
    payload = json.loads(archives.extract_from_archive(client, first.id, sidecar))
    assert "title" in payload

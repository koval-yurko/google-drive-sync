import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def config(tmp_path):
    return Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )


@pytest.fixture
def client(config):
    with TestClient(create_app(config=config, drive=FakeDrive())) as test_client:
        yield test_client


def start_a_run(app, config, *, on_disk: int, expected: int):
    run_dir = config.downloads_dir / "2026-08-10_14-32-05"
    run_dir.mkdir(parents=True)
    path = run_dir / "IMG_1.HEIC.part"
    path.write_bytes(b"x" * on_disk)
    app.state.inflight.open_run(run_dir)
    app.state.inflight.start(
        "e1", name="IMG_1.HEIC", destination="Photos/2023-11",
        expected_size=expected, path=path,
    )
    return run_dir


def test_nothing_running_reports_nothing(client):
    body = client.get("/api/downloads").json()
    assert body == {"run_dir": None, "files": [], "stale_runs": []}


def test_a_downloading_file_is_reported_with_its_bytes(client, config):
    start_a_run(client.app, config, on_disk=25, expected=100)
    body = client.get("/api/downloads").json()
    assert body["run_dir"] == "downloads/2026-08-10_14-32-05"
    assert body["files"] == [{
        "name": "IMG_1.HEIC",
        "phase": "downloading",
        "bytes": 25,
        "total": 100,
        "destination": "Photos/2023-11",
    }]


def test_a_fully_spooled_file_is_reported_as_uploading(client, config):
    start_a_run(client.app, config, on_disk=100, expected=100)
    client.app.state.inflight.uploaded("e1", 60)
    [file] = client.get("/api/downloads").json()["files"]
    assert file["phase"] == "uploading"
    assert file["bytes"] == 60


def test_an_earlier_run_holding_bytes_is_reported(client, config):
    start_a_run(client.app, config, on_disk=25, expected=100)
    orphan = config.downloads_dir / "2026-08-09_22-14-01"
    orphan.mkdir(parents=True)
    (orphan / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    body = client.get("/api/downloads").json()

    assert body["stale_runs"] == [
        {"dir": "2026-08-09_22-14-01", "files": 1, "bytes": 2048}
    ]


def test_reporting_never_deletes_anything(client, config):
    """The endpoint is a reader. Empty leftovers are the running action's job."""
    orphan = config.downloads_dir / "2026-08-09_10-00-00"
    orphan.mkdir(parents=True)
    client.get("/api/downloads")
    assert orphan.exists()

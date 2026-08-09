import os
from pathlib import Path

from photolib.config import Config


def test_load_defaults_to_repo_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOLIB_HOME", str(tmp_path))
    cfg = Config.load()
    assert cfg.repo_root == tmp_path
    assert cfg.db_path == tmp_path / "photolib.db"
    assert cfg.credentials_path == tmp_path / "credentials.json"
    assert cfg.token_path == tmp_path / "token.json"
    assert cfg.thumbnail_cache_dir == tmp_path / ".cache" / "thumbnails"


def test_load_without_env_uses_package_parent(monkeypatch):
    monkeypatch.delenv("PHOTOLIB_HOME", raising=False)
    cfg = Config.load()
    assert (cfg.repo_root / "pyproject.toml").exists()

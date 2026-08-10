"""Path and environment resolution for the application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    repo_root: Path
    db_path: Path
    credentials_path: Path
    token_path: Path
    thumbnail_cache_dir: Path
    downloads_dir: Path

    @classmethod
    def load(cls) -> "Config":
        env_home = os.environ.get("PHOTOLIB_HOME")
        root = Path(env_home) if env_home else Path(__file__).resolve().parent.parent
        return cls(
            repo_root=root,
            db_path=root / "photolib.db",
            credentials_path=root / "credentials.json",
            token_path=root / "token.json",
            thumbnail_cache_dir=root / ".cache" / "thumbnails",
            downloads_dir=root / "downloads",
        )

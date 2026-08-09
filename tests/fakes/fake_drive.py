"""In-memory stand-in for DriveClient, sharing its interface exactly."""

from __future__ import annotations

import hashlib

from photolib.drive.client import FOLDER_MIME, DriveFile
from photolib.drive.errors import DriveError, NotFoundError


class FakeDrive:
    def __init__(self) -> None:
        self._files: dict[str, DriveFile] = {}
        self._content: dict[str, bytes] = {}
        self.range_calls: list[tuple[str, int, int]] = []

    def add_folder(self, id: str, name: str, parent: str | None = None) -> DriveFile:
        folder = DriveFile(
            id=id, name=name, mimeType=FOLDER_MIME,
            parents=[parent] if parent else [],
        )
        self._files[id] = folder
        return folder

    def add_file(
        self,
        id: str,
        name: str,
        content: bytes,
        parent: str,
        mime_type: str = "application/octet-stream",
    ) -> DriveFile:
        file = DriveFile(
            id=id, name=name, mimeType=mime_type, size=len(content),
            md5Checksum=hashlib.md5(content).hexdigest(), parents=[parent],
        )
        self._files[id] = file
        self._content[id] = content
        return file

    # --- DriveClient interface ---

    def get_file(self, file_id: str) -> DriveFile:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        return self._files[file_id]

    def list_children(
        self, folder_id: str, folders_only: bool = False
    ) -> list[DriveFile]:
        children = [f for f in self._files.values() if folder_id in f.parents]
        if folders_only:
            children = [f for f in children if f.is_folder]
        return sorted(children, key=lambda f: (not f.is_folder, f.name))

    def read_range(self, file_id: str, start: int, end: int) -> bytes:
        self.range_calls.append((file_id, start, end))
        # Distinguish between unknown file (404) and known folder (cannot download)
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        if file_id not in self._content:
            # File exists but has no content (it's a folder) — Drive would return fileNotDownloadable
            raise DriveError(f"file not downloadable: {file_id}")
        return self._content[file_id][start : end + 1]

    def close(self) -> None:
        pass

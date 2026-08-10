"""In-memory stand-in for DriveClient and DriveWriter, sharing their interfaces.

Models the parts of the resumable-upload protocol that are easy to get wrong:
a chunk must start exactly where the last one ended, an unfinished session
reports how much it holds, and a session can vanish.
"""

from __future__ import annotations

import hashlib
import itertools

from photolib.drive.client import FOLDER_MIME, DriveFile
from photolib.drive.errors import DriveError, NotFoundError, TransientError
from photolib.drive.writer import SessionExpiredError


class _Session:
    def __init__(self, parent: str, name: str, size: int, properties: dict) -> None:
        self.parent = parent
        self.name = name
        self.size = size
        self.properties = properties
        self.buffer = bytearray()
        self.alive = True


class FakeDrive:
    def __init__(self) -> None:
        self._files: dict[str, DriveFile] = {}
        self._content: dict[str, bytes] = {}
        self._properties: dict[str, dict] = {}
        self._sessions: dict[str, _Session] = {}
        self._ids = itertools.count(1)
        self.range_calls: list[tuple[str, int, int]] = []
        self.trashed: list[str] = []
        self.corrupt_next_upload = False
        self.fail_chunks = 0

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

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

    def properties_of(self, file_id: str) -> dict:
        """Test helper: the appProperties recorded at upload time."""
        return self._properties.get(file_id, {})

    def expire_session(self, session_uri: str) -> None:
        """Test helper: make Drive forget a session, as it does after a week."""
        self._sessions[session_uri].alive = False

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
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        if file_id not in self._content:
            raise DriveError(f"file not downloadable: {file_id}")
        return self._content[file_id][start : end + 1]

    def close(self) -> None:
        pass

    # --- DriveWriter interface ---

    def create_folder(self, parent_id: str, name: str) -> DriveFile:
        # Drive permits duplicates here; the fake must too, or ensure_folder
        # would look correct even when it is not being used.
        return self.add_folder(self._next_id("folder"), name, parent=parent_id)

    def ensure_folder(self, parent_id: str, name: str) -> DriveFile:
        for child in self.list_children(parent_id, folders_only=True):
            if child.name == name:
                return child
        return self.create_folder(parent_id, name)

    def trash(self, file_id: str) -> None:
        if file_id not in self._files:
            raise NotFoundError(f"no such file: {file_id}")
        self.trashed.append(file_id)
        del self._files[file_id]
        self._content.pop(file_id, None)

    def start_session(
        self,
        parent_id: str,
        name: str,
        size: int,
        mime_type: str,
        properties: dict[str, str],
    ) -> str:
        if parent_id not in self._files:
            raise NotFoundError(f"no such parent: {parent_id}")
        uri = f"https://upload.fake/session/{self._next_id('s')}"
        self._sessions[uri] = _Session(parent_id, name, size, dict(properties))
        return uri

    def session_offset(self, session_uri: str, size: int) -> int:
        session = self._sessions.get(session_uri)
        if session is None or not session.alive:
            raise SessionExpiredError(f"no such session: {session_uri}")
        return len(session.buffer)

    def send_chunk(
        self, session_uri: str, chunk: bytes, start: int, total: int
    ) -> DriveFile | None:
        session = self._sessions.get(session_uri)
        if session is None or not session.alive:
            raise SessionExpiredError(f"no such session: {session_uri}")
        if self.fail_chunks > 0:
            self.fail_chunks -= 1
            raise TransientError("503: fake transient failure")
        if start != len(session.buffer):
            raise DriveError(
                f"chunk starts at {start}, session holds {len(session.buffer)}"
            )
        session.buffer.extend(chunk)
        if len(session.buffer) < total:
            return None

        content = bytes(session.buffer)
        if self.corrupt_next_upload:
            self.corrupt_next_upload = False
            content = content + b"corrupted"
        file_id = self._next_id("up")
        file = self.add_file(
            file_id, session.name, content, parent=session.parent
        )
        self._properties[file_id] = session.properties
        return file

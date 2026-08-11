"""The write half of Drive API v3: folders, resumable uploads, trashing.

Deliberately a separate class from DriveClient so it stays obvious which code
can mutate Drive. It composes rather than inherits: a writer borrows an
existing client's HTTP session and OAuth headers, and resolves them at call
time so it can be constructed around a client it never uses.

Nothing here deletes. `trash` sets `trashed = true`, which is reversible from
the Drive UI.
"""

from __future__ import annotations

import json

import httpx

from photolib.drive.client import (
    API_ROOT,
    FILE_FIELDS,
    FOLDER_MIME,
    DriveClient,
    DriveFile,
)
from photolib.drive.errors import (
    DriveError,
    TransientError,
    raise_for_response,
    retry,
)

UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3/files"

# Drive requires every chunk except the last to be a multiple of 256 KiB.
CHUNK_SIZE = 8 * 1024 * 1024

JSON_TYPE = "application/json; charset=UTF-8"


class SessionExpiredError(DriveError):
    """The resumable session is gone. Start a new one; do not retry this one."""


def _committed_bytes(response: httpx.Response) -> int:
    """Turn Drive's `Range: bytes=0-511` into the count of bytes it holds."""
    header = response.headers.get("Range")
    if not header:
        return 0                      # Drive omits Range when it has nothing
    return int(header.rsplit("-", 1)[-1]) + 1


class DriveWriter:
    def __init__(self, client: DriveClient) -> None:
        self._client = client

    @property
    def _http(self) -> httpx.Client:
        return self._client.http

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return self._client.headers(extra)

    # ---------- folders ----------

    @retry
    def create_folder(self, parent_id: str, name: str) -> DriveFile:
        response = self._http.post(
            f"{API_ROOT}/files",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps(
                {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
            ),
        )
        raise_for_response(response)
        return DriveFile.model_validate(response.json())

    def ensure_folder(self, parent_id: str, name: str) -> DriveFile:
        """Find or create a child folder.

        Drive happily creates two folders with the same name in the same
        parent, so calling this concurrently would silently split a month
        across two folders. Callers must run it sequentially.
        """
        for child in self._client.list_children(parent_id, folders_only=True):
            if child.name == name:
                return child
        return self.create_folder(parent_id, name)

    # ---------- trashing ----------

    @retry
    def trash(self, file_id: str) -> None:
        """Move a file to Drive's trash. Never a permanent delete."""
        response = self._http.patch(
            f"{API_ROOT}/files/{file_id}",
            params={"supportsAllDrives": "true"},
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps({"trashed": True}),
        )
        raise_for_response(response)

    # ---------- properties ----------

    @retry
    def update_properties(
        self, file_id: str, properties: dict[str, str | None]
    ) -> None:
        """Set or clear private appProperties on a file.

        A value of None deletes that property — the API's own convention, and
        the only way `sync_tags` can remove a tag it previously wrote.
        """
        response = self._http.patch(
            f"{API_ROOT}/files/{file_id}",
            params={"supportsAllDrives": "true", "fields": "id"},
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps({"appProperties": properties}),
        )
        raise_for_response(response)

    # ---------- moving ----------

    @retry
    def move(
        self,
        file_id: str,
        *,
        add_parent: str,
        remove_parent: str,
        name: str | None = None,
        properties: dict[str, str | None] | None = None,
    ) -> None:
        """Reparent a file — and optionally rename it and adjust its private
        appProperties — in a single metadata-only call. No bytes move."""
        body: dict = {}
        if name is not None:
            body["name"] = name
        if properties:
            body["appProperties"] = properties
        response = self._http.patch(
            f"{API_ROOT}/files/{file_id}",
            params={
                "supportsAllDrives": "true",
                "fields": "id",
                "addParents": add_parent,
                "removeParents": remove_parent,
            },
            headers=self._headers({"Content-Type": JSON_TYPE}),
            content=json.dumps(body),
        )
        raise_for_response(response)

    # ---------- resumable upload ----------

    @retry
    def start_session(
        self,
        parent_id: str,
        name: str,
        size: int,
        mime_type: str,
        properties: dict[str, str],
    ) -> str:
        """Open a resumable session and return its URI.

        The metadata — including appProperties — rides along here, so a
        completed upload is never briefly a file with no metadata.
        """
        response = self._http.post(
            UPLOAD_ROOT,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": FILE_FIELDS,
            },
            headers=self._headers({
                "Content-Type": JSON_TYPE,
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size),
            }),
            content=json.dumps({
                "name": name,
                "parents": [parent_id],
                "appProperties": properties,
            }),
        )
        raise_for_response(response)
        location = response.headers.get("Location")
        if not location:
            raise DriveError("resumable session returned no Location header")
        return location

    @retry
    def session_offset(self, session_uri: str, size: int) -> int:
        """Ask Drive how many bytes of this session it has actually committed."""
        response = self._http.put(
            session_uri,
            headers=self._headers({"Content-Range": f"bytes */{size}"}),
            content=b"",
        )
        if response.status_code in (200, 201):
            return size
        if response.status_code == 308:
            return _committed_bytes(response)
        if response.status_code in (404, 410):
            raise SessionExpiredError(
                f"session no longer exists ({response.status_code})"
            )
        raise_for_response(response)
        raise DriveError(f"unexpected {response.status_code} querying a session")

    def send_chunk(
        self, session_uri: str, chunk: bytes, start: int, total: int
    ) -> DriveFile | None:
        """PUT one chunk. Returns the file once complete, else None.

        Deliberately NOT decorated with @retry: a failed chunk may have been
        partially committed, so the caller must re-ask `session_offset` before
        sending anything else. A network failure is still typed as
        TransientError so that recovery path engages instead of the raw
        httpx error escaping the DriveError boundary.
        """
        end = start + len(chunk) - 1
        try:
            response = self._http.put(
                session_uri,
                headers=self._headers({
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Type": "application/octet-stream",
                }),
                content=chunk,
            )
        except httpx.TransportError as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code in (200, 201):
            return DriveFile.model_validate(response.json())
        if response.status_code == 308:
            return None
        if response.status_code in (404, 410):
            raise SessionExpiredError(
                f"session no longer exists ({response.status_code})"
            )
        raise_for_response(response)
        raise DriveError(f"unexpected {response.status_code} uploading a chunk")

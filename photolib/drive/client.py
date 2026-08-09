"""Thin Drive API v3 client built on httpx.

Uses raw REST rather than google-api-python-client because byte-range reads
and resumable uploads are far easier to express directly.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from photolib.drive.errors import raise_for_response, retry

API_ROOT = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,parents"


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    size: int | None = None
    md5: str | None = Field(default=None, alias="md5Checksum")
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    parents: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME


class DriveClient:
    def __init__(self, token_provider, http: httpx.Client | None = None) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._http.close()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
        if extra:
            headers.update(extra)
        return headers

    @retry
    def get_file(self, file_id: str) -> DriveFile:
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            headers=self._headers(),
        )
        raise_for_response(response)
        return DriveFile.model_validate(response.json())

    def list_children(
        self, folder_id: str, folders_only: bool = False
    ) -> list[DriveFile]:
        query = f"'{folder_id}' in parents and trashed = false"
        if folders_only:
            query += f" and mimeType = '{FOLDER_MIME}'"

        files: list[DriveFile] = []
        page_token: str | None = None
        while True:
            payload = self._list_page(query, page_token)
            files.extend(DriveFile.model_validate(f) for f in payload.get("files", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return files

    @retry
    def _list_page(self, query: str, page_token: str | None) -> dict:
        params = {
            "q": query,
            "fields": f"files({FILE_FIELDS}),nextPageToken",
            "pageSize": "1000",
            "orderBy": "folder,name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        response = self._http.get(
            f"{API_ROOT}/files", params=params, headers=self._headers()
        )
        raise_for_response(response)
        return response.json()

    @retry
    def read_range(self, file_id: str, start: int, end: int) -> bytes:
        """Read bytes `start` through `end` inclusive from a file's content."""
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=self._headers({"Range": f"bytes={start}-{end}"}),
        )
        raise_for_response(response)
        return response.content

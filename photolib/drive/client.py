"""Thin Drive API v3 client built on httpx.

Uses raw REST rather than google-api-python-client because byte-range reads
and resumable uploads are far easier to express directly.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field

from photolib.drive.errors import raise_for_response, retry

API_ROOT = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = (
    "id,name,mimeType,size,md5Checksum,createdTime,modifiedTime,parents,"
    "thumbnailLink,imageMediaMetadata(time)"
)

# Drive's thumbnailLink ends in a size directive: .../abc=s220. Swapping it is
# how you ask for a different size; there is no query parameter for it.
_SIZE_SUFFIX = re.compile(r"=s\d+(-c)?$")


class DriveFile(BaseModel):
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    size: int | None = None
    md5: str | None = Field(default=None, alias="md5Checksum")
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    created_time: str | None = Field(default=None, alias="createdTime")
    parents: list[str] = Field(default_factory=list)
    thumbnail_link: str | None = Field(default=None, alias="thumbnailLink")
    image_media_metadata: dict | None = Field(
        default=None, alias="imageMediaMetadata"
    )

    model_config = {"populate_by_name": True}

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    def capture_hint(self) -> int | None:
        """Best guess at when this was captured, in epoch seconds.

        EXIF time is the real answer where Drive extracted one; file times
        are the fallback for videos and stripped images. None means Drive
        knows nothing datable about this file.
        """
        exif = (self.image_media_metadata or {}).get("time")
        if exif:
            try:
                parsed = datetime.strptime(exif, "%Y:%m:%d %H:%M:%S")
                return int(parsed.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                pass
        for stamp in (self.modified_time, self.created_time):
            if not stamp:
                continue
            try:
                return int(
                    datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                )
            except ValueError:
                continue
        return None


class DriveClient:
    def __init__(self, token_provider, http: httpx.Client | None = None) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(timeout=60.0)

    def close(self) -> None:
        self._http.close()

    @property
    def http(self) -> httpx.Client:
        """The shared session. DriveWriter borrows this rather than opening its own."""
        return self._http

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
        if extra:
            headers.update(extra)
        return headers

    @retry
    def get_file(self, file_id: str) -> DriveFile:
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
            headers=self.headers(),
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
            f"{API_ROOT}/files", params=params, headers=self.headers()
        )
        raise_for_response(response)
        return response.json()

    @retry
    def read_range(self, file_id: str, start: int, end: int) -> bytes:
        """Read bytes `start` through `end` inclusive from a file's content."""
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers=self.headers({"Range": f"bytes={start}-{end}"}),
        )
        raise_for_response(response)
        return response.content

    def fetch_thumbnail(self, file_id: str, size: int) -> bytes | None:
        """Drive's own render of a file, or None if it has not made one.

        Chrome cannot display HEIC, and 591 of these files are HEIC, so this
        is how the Library shows anything at all. A missing thumbnailLink is
        ordinary — Drive generates them asynchronously after upload — and is
        reported as None rather than raised.
        """
        link = self.get_file(file_id).thumbnail_link
        if not link:
            return None
        if _SIZE_SUFFIX.search(link):
            link = _SIZE_SUFFIX.sub(f"=s{size}", link)
        else:
            link = f"{link}=s{size}"
        return self._fetch_thumbnail_bytes(link)

    @retry
    def _fetch_thumbnail_bytes(self, link: str) -> bytes:
        response = self._http.get(link, headers=self.headers())
        raise_for_response(response)
        return response.content

    @retry
    def app_properties(self, file_id: str) -> dict[str, str]:
        """The file's private appProperties. `sync_tags` diffs against these."""
        response = self._http.get(
            f"{API_ROOT}/files/{file_id}",
            params={"fields": "appProperties", "supportsAllDrives": "true"},
            headers=self.headers(),
        )
        raise_for_response(response)
        return response.json().get("appProperties") or {}

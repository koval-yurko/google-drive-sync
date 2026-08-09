"""Read and update application settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef

router = APIRouter(tags=["settings"])
ALLOWED_KEYS = {PHOTOS_ROOT, ZIP_SOURCE}


@router.get("/settings")
def read_settings(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        PHOTOS_ROOT: settings.get_folder(PHOTOS_ROOT),
        ZIP_SOURCE: settings.get_folder(ZIP_SOURCE),
        "credentials_configured": request.app.state.tokens.is_configured(),
    }


@router.put("/settings/{key}")
def write_setting(key: str, folder: FolderRef, request: Request) -> FolderRef:
    if key not in ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown setting: {key}")
    request.app.state.settings.set_folder(key, folder)
    return folder

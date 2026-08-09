"""Drive browsing endpoints backing the folder picker."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from photolib.drive.errors import DriveError, NotFoundError

router = APIRouter(tags=["drive"])


@router.get("/drive/folders")
def list_folders(request: Request, parent: str = "root") -> dict:
    drive = request.app.state.drive
    try:
        folders = drive.list_children(parent, folders_only=True)
        try:
            parent_file = drive.get_file(parent)
            parent_info = {"id": parent_file.id, "name": parent_file.name}
        except NotFoundError:
            if parent != "root":
                raise
            parent_info = {"id": "root", "name": "My Drive"}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DriveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "parent": parent_info,
        "folders": [f.model_dump(by_alias=True) for f in folders],
    }

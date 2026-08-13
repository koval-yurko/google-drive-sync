"""Read-only endpoints backing the Review page."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

router = APIRouter(tags=["review"])

ROW_FIELDS = (
    # The Review page's Retry button posts this back to
    # /review/retry/{entry_id}; without it the request reads `undefined`.
    "entry_id",
    "name", "path", "archive_name", "target_folder", "target_name",
    "capture_time", "capture_source", "country",
    "duplicate_of", "duplicate_reason", "upload_status",
    "error", "drive_file_id", "plan_verdict", "plan_match",
)


@router.get("/review/summary")
def summary(request: Request) -> dict:
    conn = request.app.state.conn
    return {**MediaRepo(conn).summary(), **ScanRepo(conn).counts()}


@router.get("/review/media")
def media(
    request: Request,
    limit: int = 200,
    offset: int = 0,
    folder: str | None = None,
    duplicates_only: bool = False,
) -> dict:
    page = MediaRepo(request.app.state.conn).review_page(
        folder=folder,
        duplicates_only=duplicates_only,
        limit=limit,
        offset=offset,
    )
    return {
        "total": page["total"],
        "rows": [
            {**{f: row[f] for f in ROW_FIELDS}, "size": row["entry_size"]}
            for row in page["rows"]
        ],
    }


@router.post("/review/retry/{entry_id}")
def retry(request: Request, entry_id: int) -> dict:
    """Queue a failed file for another attempt, forgetting the last one."""
    repo = MediaRepo(request.app.state.conn)
    if not repo.exists(entry_id):
        raise HTTPException(status_code=404, detail="no such media entry")
    repo.reset_upload(entry_id)
    return {"entry_id": entry_id, "upload_status": "pending"}

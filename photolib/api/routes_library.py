"""Read-only endpoints backing the Library page.

Nothing here writes. Tagging lives in `routes_tags`, and the only thing that
touches Drive is the `sync_tags` action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from photolib.db.library_repo import Filters, LibraryRepo
from photolib.db.tags_repo import TagsRepo

router = APIRouter(tags=["library"])


def _filters(
    month: str | None = None,
    country: str | None = None,
    media_type: str | None = Query(default=None, pattern="^(image|video|other)$"),
    tag_id: int | None = None,
    duplicates: bool = False,
    search: str | None = None,
) -> Filters:
    return Filters(
        month=month, country=country, media_type=media_type,
        tag_id=tag_id, duplicates=duplicates, search=search,
    )


@router.get("/library/files")
def files(
    request: Request,
    filters: Filters = Depends(_filters),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    conn = request.app.state.conn
    result = LibraryRepo(conn).list_files(filters, limit=limit, offset=offset)
    tags = TagsRepo(conn).tags_for([row["drive_id"] for row in result["rows"]])
    return {
        "total": result["total"],
        "rows": [
            {**row, "tags": tags.get(row["drive_id"], [])} for row in result["rows"]
        ],
    }


@router.get("/library/ids")
def ids(request: Request, filters: Filters = Depends(_filters)) -> dict:
    """Every id matching the filter — what 'select all matching' selects."""
    return {"ids": LibraryRepo(request.app.state.conn).all_ids(filters)}


@router.get("/library/facets")
def facets(request: Request) -> dict:
    return LibraryRepo(request.app.state.conn).facets()


@router.get("/library/file/{drive_id}")
def file_detail(request: Request, drive_id: str) -> dict:
    conn = request.app.state.conn
    row = LibraryRepo(conn).detail(drive_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such file in the library")
    return {**row, "tags": TagsRepo(conn).tags_for([drive_id]).get(drive_id, [])}

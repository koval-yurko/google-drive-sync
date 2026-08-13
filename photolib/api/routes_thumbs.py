"""The image proxy. Drive holds the renders; this hands them to the browser."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response

from photolib.drive.errors import NotFoundError
from photolib.drive.thumbs import ThumbnailUnavailable

router = APIRouter(tags=["thumbs"])


@router.get("/thumb/{drive_id}")
def thumb(request: Request, drive_id: str, size: int = Query(default=400)) -> Response:
    cache = request.app.state.thumbnails
    try:
        content = cache.get(drive_id, size)
    except ValueError as exc:
        # An unknown size or a malformed id: the caller's fault, not Drive's.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThumbnailUnavailable:
        # Not an error. Drive renders asynchronously after upload; the tile
        # shows a placeholder and asks again later.
        return Response(status_code=202)

    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )

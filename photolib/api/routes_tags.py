"""Tag CRUD and bulk assignment. SQLite only — nothing here touches Drive.

Drive learns about tags when you run the `sync_tags` action, which reports
what it would change before it changes anything. Keeping the two apart is
what makes tagging in the UI instant and free of failure modes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from photolib.db.tags_repo import DEFAULT_COLOR, DuplicateTagError, TagsRepo

router = APIRouter(tags=["tags"])


class TagCreate(BaseModel):
    name: str
    color: str = DEFAULT_COLOR


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None


class FileList(BaseModel):
    drive_ids: list[str] = Field(default_factory=list)


class MergeRequest(BaseModel):
    source_id: int
    target_id: int


def _repo(request: Request) -> TagsRepo:
    return TagsRepo(request.app.state.conn)


def _row(repo: TagsRepo, tag_id: int) -> dict:
    """One tag with its count, so create and patch answer in the list's shape."""
    for row in repo.list_with_counts():
        if row["id"] == tag_id:
            return dict(row)
    raise HTTPException(status_code=404, detail="no such tag")


def _require(repo: TagsRepo, tag_id: int) -> None:
    if repo.get(tag_id) is None:
        raise HTTPException(status_code=404, detail="no such tag")


@router.get("/tags")
def list_tags(request: Request) -> list[dict]:
    return [dict(row) for row in _repo(request).list_with_counts()]


@router.post("/tags", status_code=201)
def create_tag(request: Request, body: TagCreate) -> dict:
    repo = _repo(request)
    try:
        tag = repo.create(body.name, body.color)
    except DuplicateTagError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _row(repo, tag["id"])


@router.patch("/tags/{tag_id}")
def patch_tag(request: Request, tag_id: int, body: TagPatch) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    if body.name is None and body.color is None:
        raise HTTPException(status_code=422, detail="give a name, a color, or both")
    try:
        if body.name is not None:
            repo.rename(tag_id, body.name)
        if body.color is not None:
            repo.recolor(tag_id, body.color)
    except DuplicateTagError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _row(repo, tag_id)


@router.delete("/tags/{tag_id}")
def delete_tag(request: Request, tag_id: int) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    repo.delete(tag_id)
    return {"deleted": tag_id}


@router.post("/tags/merge")
def merge_tags(request: Request, body: MergeRequest) -> dict:
    repo = _repo(request)
    _require(repo, body.source_id)
    _require(repo, body.target_id)
    try:
        moved = repo.merge(body.source_id, body.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"moved": moved, "target": _row(repo, body.target_id)}


@router.post("/tags/{tag_id}/files")
def add_files(request: Request, tag_id: int, body: FileList) -> dict:
    repo = _repo(request)
    _require(repo, tag_id)
    return {"added": repo.add_files(tag_id, body.drive_ids)}


@router.post("/tags/{tag_id}/files/remove")
def remove_files(request: Request, tag_id: int, body: FileList) -> dict:
    """A POST, not a DELETE with a body — 1,284 ids do not fit in a URL."""
    repo = _repo(request)
    _require(repo, tag_id)
    return {"removed": repo.remove_files(tag_id, body.drive_ids)}

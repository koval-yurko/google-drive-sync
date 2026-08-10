"""Read-only endpoints backing the Review page."""

from __future__ import annotations

from fastapi import APIRouter, Request

from photolib.db.media_repo import MediaRepo
from photolib.db.scan_repo import ScanRepo

router = APIRouter(tags=["review"])

ROW_FIELDS = (
    "name", "path", "archive_name", "target_folder", "target_name",
    "capture_time", "capture_source", "place", "country",
    "duplicate_of", "duplicate_reason", "upload_status",
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
    conn = request.app.state.conn

    where, args = [], []
    if folder:
        where.append("m.target_folder = ?")
        args.append(folder)
    if duplicates_only:
        where.append("m.duplicate_of IS NOT NULL")
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM media m {clause}", args
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT m.*, e.path, e.name, e.size AS entry_size, "
        "       a.name AS archive_name "
        "FROM media m "
        "JOIN entries e ON e.id = m.entry_id "
        "JOIN archives a ON a.id = e.archive_id "
        f"{clause} "
        "ORDER BY m.target_folder, m.target_name "
        "LIMIT ? OFFSET ?",
        [*args, limit, offset],
    )

    return {
        "total": total,
        "rows": [
            {**{f: row[f] for f in ROW_FIELDS}, "size": row["entry_size"]}
            for row in rows
        ],
    }

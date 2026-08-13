"""What is moving through the downloads folder right now."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from photolib.execution.downloads import observe, stale_runs

router = APIRouter(tags=["downloads"])


@router.get("/downloads")
def list_downloads(request: Request) -> dict:
    registry = request.app.state.inflight
    root = request.app.state.config.downloads_dir
    run_dir = registry.run_dir
    return {
        "run_dir": f"{root.name}/{run_dir.name}" if run_dir else None,
        "files": [asdict(view) for view in observe(registry.snapshot())],
        "stale_runs": stale_runs(root, active=run_dir),
    }

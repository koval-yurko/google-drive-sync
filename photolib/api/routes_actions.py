"""List available actions and launch them as jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from photolib.actions.registry import UnknownActionError, all_actions, get_action

router = APIRouter(tags=["actions"])


@router.get("/actions")
def list_actions() -> list[dict]:
    return [
        {
            "id": spec.id,
            "title": spec.title,
            "description": spec.description,
            "order": spec.order,
            "group": spec.group,
            "schema": spec.json_schema(),
        }
        for spec in all_actions()
    ]


@router.post("/actions/{action_id}/run")
def run_action(action_id: str, params: dict, request: Request) -> dict:
    try:
        spec = get_action(action_id)
    except UnknownActionError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown action: {action_id}"
        ) from exc

    try:
        spec.params_model.model_validate(params)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False)
        ) from exc

    try:
        job = request.app.state.runner.submit(action_id, params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return job.model_dump()

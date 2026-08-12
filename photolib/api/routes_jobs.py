"""Job history, detail, and the live event stream."""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from photolib.actions.registry import UnknownActionError, get_action
from photolib.db.job_items_repo import JobItemsRepo

router = APIRouter(tags=["jobs"])
POLL_INTERVAL = 0.25
RESUMABLE = {"failed", "cancelled"}


@router.get("/jobs")
def list_jobs(request: Request, limit: int = 50) -> list[dict]:
    return [job.model_dump() for job in request.app.state.jobs.list(limit)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job.model_dump()


@router.get("/jobs/{job_id}/events")
def get_events(job_id: str, request: Request, after: int = 0) -> list[dict]:
    if request.app.state.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")
    return [e.model_dump() for e in request.app.state.jobs.events(job_id, after)]


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    jobs = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if not request.app.state.runner.cancel(job_id):
        # Re-read: the job may have finished in the window between the
        # lookup above and cancel() giving up, so the pre-cancel status
        # could already be stale by the time we report why it was rejected.
        current = jobs.get(job_id)
        status = current.status if current is not None else job.status
        raise HTTPException(
            status_code=409, detail=f"job is already {status}"
        )
    return jobs.get(job_id).model_dump()


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if job.status not in RESUMABLE:
        raise HTTPException(
            status_code=409,
            detail=f"only failed or cancelled jobs resume; this one is {job.status}",
        )
    try:
        spec = get_action(job.action)
    except UnknownActionError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown action: {job.action}"
        ) from exc

    params = dict(job.params)
    # ActionParams forbids extras, so only actions that declare run_id get it.
    if "run_id" in spec.params_model.model_fields:
        params["run_id"] = job.run_id

    resumed = request.app.state.runner.submit(
        job.action, params, run_id=job.run_id, resumed_from=job.id
    )
    return resumed.model_dump()


@router.get("/jobs/{job_id}/items")
def job_items(
    job_id: str,
    request: Request,
    phase: str | None = None,
    state: str | None = None,
) -> list[dict]:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return JobItemsRepo(request.app.state.conn).all(job.run_id, phase, state)


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    jobs = request.app.state.jobs
    broker = request.app.state.broker
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")

    subscription = broker.subscribe(job_id)

    async def publisher():
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = subscription.get_nowait()
                except queue.Empty:
                    job = jobs.get(job_id)
                    if job and job.status in {"done", "failed", "cancelled"}:
                        yield {"event": "end", "data": job.model_dump_json()}
                        return
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                yield {"event": "message", "data": json.dumps(payload)}
        finally:
            broker.unsubscribe(job_id, subscription)

    return EventSourceResponse(publisher())

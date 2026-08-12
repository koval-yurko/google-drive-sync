"""Job history, detail, and the live event stream."""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["jobs"])
POLL_INTERVAL = 0.25


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

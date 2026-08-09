"""A single background worker that executes queued actions."""

from __future__ import annotations

import queue
import threading
import traceback

from photolib.actions import registry
from photolib.db.jobs_repo import Job, JobsRepo
from photolib.jobs.broker import EventBroker


class JobRunner:
    def __init__(self, context_factory, repo: JobsRepo, broker: EventBroker) -> None:
        self._context_factory = context_factory
        self._repo = repo
        self.broker = broker
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._outstanding = 0
        self._outstanding_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        self._thread = None

    def submit(self, action_id: str, params: dict) -> Job:
        registry.get_action(action_id)  # fail fast on unknown ids
        job = self._repo.create(action_id, params)
        with self._outstanding_lock:
            self._outstanding += 1
            self._idle.clear()
        self._queue.put(job.id)
        return job

    def wait_idle(self, timeout: float = 5.0) -> None:
        if not self._idle.wait(timeout):
            raise TimeoutError("job runner did not become idle")

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._execute(job_id)
            finally:
                with self._outstanding_lock:
                    self._outstanding -= 1
                    if self._outstanding == 0:
                        self._idle.set()

    def _execute(self, job_id: str) -> None:
        job = self._repo.get(job_id)
        if job is None:
            return
        self._repo.mark_running(job_id)
        self._emit(job_id, {"type": "status", "status": "running"})
        try:
            spec = registry.get_action(job.action)
            params = spec.params_model.model_validate(job.params)
            for event in spec.run(self._context_factory(), params):
                self._repo.add_event(job_id, event.level, event.message)
                if event.progress is not None:
                    self._repo.update_progress(job_id, event.progress, event.message)
                self._emit(job_id, {
                    "type": "event", "level": event.level,
                    "message": event.message, "progress": event.progress,
                })
            self._repo.mark_done(job_id)
            self._emit(job_id, {"type": "status", "status": "done"})
        except Exception as exc:
            detail = f"{exc}\n{traceback.format_exc()}"
            self._repo.mark_failed(job_id, detail)
            self._repo.add_event(job_id, "error", str(exc))
            self._emit(job_id, {
                "type": "status", "status": "failed", "error": str(exc),
            })

    def _emit(self, job_id: str, payload: dict) -> None:
        self.broker.publish(job_id, payload)

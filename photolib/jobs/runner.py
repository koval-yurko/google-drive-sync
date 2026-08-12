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
        self._cancels: dict[str, threading.Event] = {}
        self._cancels_lock = threading.Lock()

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
        if self._thread.is_alive():
            raise RuntimeError(
                "job runner worker thread did not stop within 5.0s timeout"
            )
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

    def cancel(self, job_id: str) -> bool:
        """Ask a queued or running job to stop. False if it is already over."""
        job = self._repo.get(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return False

        if job.status == "queued":
            # It may never reach _execute if the runner is stopped, so
            # settle it now. The guarded update only wins while the job is
            # still queued or running: a fast action can race ahead to
            # done/failed between our read above and this call, and in that
            # case it must be left alone rather than overwritten. _execute
            # hasn't necessarily registered an event for this job yet (it
            # hasn't started), so we create one here for it to find.
            with self._cancels_lock:
                event = self._cancels.setdefault(job_id, threading.Event())
            event.set()
            if not self._repo.mark_cancelled(job_id):
                with self._cancels_lock:
                    self._cancels.pop(job_id, None)
                return False
            self._emit(job_id, {"type": "status", "status": "cancelled"})
            return True

        # job.status == "running": leave the DB transition to _execute,
        # which observes the event from inside its own loop. Marking it
        # cancelled from here too would race _execute's own terminal write
        # (mark_done/mark_failed is unguarded, so it could stomp right back
        # over a 'cancelled' we set concurrently).
        #
        # _execute registers its cancel event *before* calling mark_running
        # (see below), so a row we just read as "running" guarantees that
        # entry already exists in `self._cancels` — unless the job has since
        # finished for real and its `finally` already popped it, which is
        # exactly the race we need to detect. A plain (non-creating) lookup
        # tells the two cases apart without a second trip to the database:
        # if the entry is gone, the job is over and there is nothing to
        # leak or to cancel.
        with self._cancels_lock:
            event = self._cancels.get(job_id)
            if event is None:
                return False
            event.set()
        return True

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
        try:
            with self._cancels_lock:
                cancel = self._cancels.setdefault(job.id, threading.Event())

            if cancel.is_set():
                # Cancelled before we ever marked it running — e.g. cancel()
                # already settled it while the runner was stopped, or it
                # raced ahead of us here. Check this *before* mark_running so
                # a queued-then-cancelled job never emits a `running` status
                # and never gets a started_at. The guarded update is a no-op
                # if cancel() (or a previous pass through this branch)
                # already made the transition, so we don't double-emit.
                if self._repo.mark_cancelled(job_id):
                    self._emit(job_id, {"type": "status", "status": "cancelled"})
                return

            self._repo.mark_running(job_id)
            self._emit(job_id, {"type": "status", "status": "running"})

            spec = registry.get_action(job.action)
            params = spec.params_model.model_validate(job.params)

            ctx = self._context_factory()
            ctx.run_id = job.run_id
            ctx.cancelled = cancel

            generator = spec.run(ctx, params)
            for event in generator:
                self._repo.add_event(job_id, event.level, event.message)
                if event.progress is not None:
                    self._repo.update_progress(
                        job_id, event.progress, event.message,
                        phase=event.phase, done=event.done, total=event.total,
                    )
                self._emit(job_id, {
                    "type": "event", "level": event.level,
                    "message": event.message, "progress": event.progress,
                    "phase": event.phase, "done": event.done,
                    "total": event.total,
                })
                if cancel.is_set():
                    # Closing raises GeneratorExit at the yield, so the
                    # action's `finally` blocks run and job_items survive.
                    generator.close()
                    if self._repo.mark_cancelled(job_id):
                        self._repo.add_event(job_id, "warn", "Cancelled.")
                        self._emit(job_id, {
                            "type": "status", "status": "cancelled",
                        })
                    return
            self._repo.mark_done(job_id)
            self._emit(job_id, {"type": "status", "status": "done"})
        except Exception as exc:
            detail = f"{exc}\n{traceback.format_exc()}"
            self._repo.mark_failed(job_id, detail)
            self._repo.add_event(job_id, "error", str(exc))
            self._emit(job_id, {
                "type": "status", "status": "failed", "error": str(exc),
            })
        finally:
            with self._cancels_lock:
                self._cancels.pop(job_id, None)

    def _emit(self, job_id: str, payload: dict) -> None:
        self.broker.publish(job_id, payload)

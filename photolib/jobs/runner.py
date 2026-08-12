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

    def submit(
        self,
        action_id: str,
        params: dict,
        run_id: str | None = None,
        resumed_from: str | None = None,
    ) -> Job:
        registry.get_action(action_id)  # fail fast on unknown ids
        # An action that declares `run_id` in its Params is asking to continue
        # an existing run — a flow confirming the plan it just reported. An
        # explicit argument (from resume) still wins.
        job = self._repo.create(
            action_id, params, run_id or params.get("run_id"), resumed_from
        )
        with self._outstanding_lock:
            self._outstanding += 1
            self._idle.clear()
        self._queue.put(job.id)
        return job

    def wait_idle(self, timeout: float = 5.0) -> None:
        if not self._idle.wait(timeout):
            raise TimeoutError("job runner did not become idle")

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a queued or running job.

        This is a *request*, not an instantaneous transition: for a running
        job the request lands at the next item boundary (the next place the
        action yields), not synchronously inside this call. Returns `False`
        only when the job was already terminal (done/failed/cancelled, or
        unknown) *when this request was processed* — not a promise about
        the job's state by the time this call returns. Because the job can
        legitimately finish on its own in the same window this call is
        racing through, `True` does not guarantee the job will end up
        `cancelled`: a job that finishes first legitimately wins, and that
        is not a bug (see the two TOCTOU windows documented inline below).
        The one invariant that *is* guaranteed, enforced by the guarded
        `JobsRepo.mark_cancelled`: this call can never move a job that has
        already reached a terminal status off of that status. Callers that
        need the authoritative outcome — including the `POST
        /jobs/{id}/cancel` route — must read the job row after calling this,
        not trust this return value as the final word.
        """
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
        #
        # Window 1 (accepted, not closed): between _execute's mark_done /
        # mark_failed / mark_cancelled write and its `finally` popping this
        # entry, the entry still exists. A concurrent cancel() that reads
        # "running" and reaches this lookup inside that (very small) window
        # gets a live event and returns True even though the row is already
        # terminal — a "misleading True". Closing it would require a lock
        # spanning a database write and the registry mutation, which we are
        # not willing to pay for a request-vs-outcome distinction the API
        # already handles correctly: the route re-reads the job row and
        # returns *that*, so a client never sees a wrong status, only a
        # `cancel()` return value that overstated what actually happened.
        # The invariant that must hold — cancel() can never move an already
        # terminal job off its terminal status — is enforced by the guarded
        # UPDATE in mark_cancelled and covered by
        # test_cancel_of_a_done_job_never_moves_it_off_terminal_status.
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

            # Window 2 (accepted, not closed): cancel() can read "queued"
            # and set the event *after* we have already passed the check
            # above but *before* the action's first yield. In that case the
            # action body runs and reaches its first item boundary before
            # this job is stopped. This is not a bug: the brief's design is
            # that cancellation lands on item boundaries, so once an action
            # has started, letting it reach the next yield (rather than
            # aborting mid-item) is required, not accidental. The in-loop
            # check further down still stops it there; no status corruption
            # results either way, since mark_cancelled is guarded.

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
                    break

            if cancel.is_set():
                # Reached either by the `break` above, or because the `for`
                # loop simply ran out of events. The two flows notice
                # cancellation themselves at an item boundary and `return`
                # from their generator rather than waiting to be closed
                # (see sync_archives.py, reorganize_library.py) — that ends
                # this loop exactly like a normal, uncancelled finish, so
                # only this flag, never how the loop ended, can tell the two
                # apart. Getting this branch wrong records a cancelled run
                # as `done`.
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

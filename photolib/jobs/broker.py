"""In-process fan-out of job events to SSE subscribers."""

from __future__ import annotations

import queue
import threading


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self._lock:
            if job_id in self._subscribers and q in self._subscribers[job_id]:
                self._subscribers[job_id].remove(q)
            if job_id in self._subscribers and not self._subscribers[job_id]:
                del self._subscribers[job_id]

    def publish(self, job_id: str, payload: dict) -> None:
        with self._lock:
            targets = list(self._subscribers.get(job_id, []))
        for q in targets:
            q.put(payload)

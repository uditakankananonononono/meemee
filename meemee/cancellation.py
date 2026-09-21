from __future__ import annotations

import threading


class CancellationRegistry:
    """Thread-safe cooperative cancellation registry for active local jobs."""

    def __init__(self):
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def register(self, job_id: str) -> threading.Event:
        with self._lock:
            if job_id in self._events:
                raise ValueError(f"job already registered: {job_id}")
            event = threading.Event()
            self._events[job_id] = event
            return event

    def request(self, job_id: str) -> bool:
        with self._lock:
            event = self._events.get(job_id)
            if event is None:
                return False
            event.set()
            return True

    def unregister(self, job_id: str) -> None:
        with self._lock:
            self._events.pop(job_id, None)

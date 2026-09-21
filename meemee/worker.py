from __future__ import annotations

import asyncio
import threading

from .config import Settings
from .jobs import JobStore
from .runtime import build_agent


def cancellation_watcher(jobs: JobStore, job_id: str, event: threading.Event, stop: threading.Event) -> None:
    while not stop.wait(0.25):
        job = jobs.get(job_id)
        if job and job["status"] == "cancel_requested":
            event.set()
            return


async def work_forever(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    jobs = JobStore(settings.data_dir / "jobs.sqlite3")
    while True:
        job = jobs.claim()
        if job is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        cancel, stop = threading.Event(), threading.Event()
        watcher = threading.Thread(target=cancellation_watcher, args=(jobs, job["id"], cancel, stop), daemon=True)
        watcher.start()
        try:
            report = await build_agent(settings).run(job["goal"], cancel=cancel)
            if cancel.is_set():
                jobs.cancel_running(job["id"])
            else:
                jobs.finish(job["id"], report.model_dump())
        except (OSError, ValueError, RuntimeError) as exc:
            jobs.fail(job["id"], str(exc))
        finally:
            stop.set(); watcher.join(timeout=1)

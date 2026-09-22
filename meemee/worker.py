from __future__ import annotations

import asyncio
import threading

from .config import Settings
from .persistence import build_persistence
from .runtime import build_agent
from .webhooks import WebhookStore


def cancellation_watcher(jobs, job_id: str, event: threading.Event, stop: threading.Event) -> None:
    while not stop.wait(0.25):
        job = jobs.get(job_id)
        if job and job["status"] == "cancel_requested":
            event.set()
            return


async def work_forever(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    persistence = build_persistence(settings.persistence_backend, settings.data_dir, settings.postgres_dsn)
    jobs = persistence.jobs
    webhooks = WebhookStore(
        settings.data_dir / "webhooks.sqlite3",
        settings.webhook_max_payload_bytes,
        settings.vault_key,
    )
    while True:
        job = jobs.claim()
        if job is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        cancel, stop = threading.Event(), threading.Event()
        watcher = threading.Thread(target=cancellation_watcher, args=(jobs, job["id"], cancel, stop), daemon=True)
        watcher.start()
        try:
            report = await build_agent(settings, memory=persistence.memory).run(job["goal"], cancel=cancel)
            if cancel.is_set():
                jobs.cancel_running(job["id"])
                webhooks.enqueue(f"job:{job['id']}:cancelled", "job.cancelled", {"job_id": job["id"], "status": "cancelled"})
            else:
                jobs.finish(job["id"], report.model_dump())
                webhooks.enqueue(f"job:{job['id']}:done", "job.done", {"job_id": job["id"], "status": "done", "result": report.model_dump()})
        except (OSError, ValueError, RuntimeError) as exc:
            jobs.fail(job["id"], str(exc))
            state = jobs.get(job["id"])["status"]
            if state == "failed":
                webhooks.enqueue(f"job:{job['id']}:failed", "job.failed", {"job_id": job["id"], "status": "failed", "error": str(exc)})
        finally:
            stop.set(); watcher.join(timeout=1)

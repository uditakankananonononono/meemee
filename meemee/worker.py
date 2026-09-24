from __future__ import annotations

import asyncio
import threading
import uuid

from .approvals import ApprovalStore
from .config import Settings
from .persistence import persistence_from_settings
from .runtime import build_agent
from .webhooks import WebhookStore


def cancellation_watcher(jobs, job_id: str, event: threading.Event, stop: threading.Event) -> None:
    while not stop.wait(0.25):
        job = jobs.get(job_id)
        if job and job["status"] == "cancel_requested":
            event.set()
            return


def purge_if_deleted(jobs, memory, job_id: str, run_id: str) -> bool:
    """After a terminal call, finish account deletion for a job purged mid-run.

    The job row is gone when its owner deleted their account while it ran; the run's
    memories were written after the purge snapshot, so they are removed here.
    """
    if jobs.get(job_id) is not None:
        return False
    memory.delete_runs([run_id])
    return True


def job_approval(approvals: ApprovalStore, owner: str):
    """Approval callback for a queued job: only the owner's active persistent grants count."""
    return lambda name, arguments, _risk: approvals.allows(owner, name, arguments=arguments)


async def work_forever(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    persistence = persistence_from_settings(settings)
    jobs = persistence.jobs
    webhooks = WebhookStore(
        settings.data_dir / "webhooks.sqlite3",
        settings.webhook_max_payload_bytes,
        settings.vault_key,
    )
    # Same persistent, argument-scoped grants the API applies to POST /v1/runs. Queued jobs have no
    # per-request approval fields, so a grant is the only way a job may use an approval-gated tool.
    # PostgreSQL mode reads them from the shared database, so a grant made on the API host applies here.
    approvals = persistence.approvals
    while True:
        job = jobs.claim()
        if job is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        cancel, stop = threading.Event(), threading.Event()
        watcher = threading.Thread(target=cancellation_watcher, args=(jobs, job["id"], cancel, stop), daemon=True)
        watcher.start()
        run_id = uuid.uuid4().hex
        try:
            owner = job.get("principal") or "default"
            report = await build_agent(settings, memory=persistence.memory).run(
                job["goal"], approve=job_approval(approvals, owner), cancel=cancel, owner_id=owner, run_id=run_id)
            if cancel.is_set():
                jobs.cancel_running(job["id"])
                if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id):
                    webhooks.enqueue(f"job:{job['id']}:cancelled", "job.cancelled", {"job_id": job["id"], "status": "cancelled"}, principal=owner)
            else:
                jobs.finish(job["id"], report.model_dump())
                if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id):
                    webhooks.enqueue(f"job:{job['id']}:done", "job.done", {"job_id": job["id"], "status": "done", "blocked": report.blocked, "result": report.model_dump()}, principal=owner)
        except (OSError, ValueError, RuntimeError) as exc:
            jobs.fail(job["id"], str(exc))
            if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id):
                state = jobs.get(job["id"])["status"]
                if state == "failed":
                    webhooks.enqueue(f"job:{job['id']}:failed", "job.failed", {"job_id": job["id"], "status": "failed", "error": str(exc)}, principal=owner)
        finally:
            stop.set(); watcher.join(timeout=1)

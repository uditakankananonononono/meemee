from __future__ import annotations

import asyncio
import threading
import uuid

from .approvals import ApprovalStore
from .config import Settings
from .job_errors import LeaseLostError
from .persistence import persistence_from_settings
from .runtime import build_agent
from .webhooks import WebhookStore


def cancellation_watcher(jobs, job_id: str, event: threading.Event, stop: threading.Event,
                         lease_token: str | None = None, lease_lost: threading.Event | None = None) -> None:
    interval = min(0.25, float(getattr(jobs, "lease_seconds", 60)) / 3) if lease_token else 0.25
    while not stop.wait(max(interval, 0.01)):
        try:
            if lease_token and not jobs.heartbeat(job_id, lease_token):
                lease_lost.set()
                event.set()
                return
            job = jobs.get(job_id)
            if job and job["status"] == "cancel_requested":
                event.set()
                # Keep renewing leased ownership while cancellation settles.
                if not lease_token:
                    return
        except (OSError, ValueError, RuntimeError):
            if lease_token:
                lease_lost.set()
                event.set()
            return


def purge_if_deleted(jobs, memory, job_id: str, run_id: str, owner_id: str) -> bool:
    """After a terminal call, finish account deletion for a job purged mid-run.

    The job row is gone when its owner deleted their account while it ran; the run's
    memories were written after the purge snapshot, so they are removed here.
    """
    if jobs.get(job_id) is not None:
        return False
    memory.delete_runs([run_id], owner_id=owner_id)
    return True


def job_approval(approvals: ApprovalStore, owner: str):
    """Approval callback for a queued job: only the owner's active persistent grants count."""
    return lambda name, arguments, _risk: approvals.allows(owner, name, arguments=arguments)


async def work_forever(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    persistence = persistence_from_settings(settings)
    try:
        jobs = persistence.jobs
        webhooks = persistence.webhooks or WebhookStore(
            settings.data_dir / "webhooks.sqlite3",
            settings.webhook_max_payload_bytes,
            settings.vault_key,
        )  # PostgreSQL mode: the shared outbox
        # Same persistent, argument-scoped grants the API applies to POST /v1/runs. Queued jobs have no
        # per-request approval fields, so a grant is the only way a job may use an approval-gated tool.
        # PostgreSQL mode reads them from the shared database, so a grant made on the API host applies here.
        approvals = persistence.approvals
        while True:
            job = jobs.claim()
            if job is None:
                await asyncio.sleep(settings.worker_poll_seconds)
                continue
            cancel, stop, lease_lost = threading.Event(), threading.Event(), threading.Event()
            lease_token = job.get("lease_token")
            terminal_kwargs = {"lease_token": lease_token} if lease_token else {}
            watcher = threading.Thread(target=cancellation_watcher,
                args=(jobs, job["id"], cancel, stop, lease_token, lease_lost), daemon=True)
            watcher.start()
            run_id = uuid.uuid4().hex
            try:
                owner = job.get("principal")
                if not owner:
                    # Fail closed: every creation path sets a principal, so a
                    # principal-less job is legacy or corrupt. Running it under the
                    # shared "default" bucket would leak its memory across owners.
                    jobs.fail(job["id"], "job has no principal owner; refusing to run unowned work", **terminal_kwargs)
                    continue
                report = await build_agent(settings, memory=persistence.memory, persistence=persistence).run(
                    job["goal"], approve=job_approval(approvals, owner), cancel=cancel, owner_id=owner, run_id=run_id)
                if lease_lost.is_set():
                    # A different claimant may own the row. Never fail, finish,
                    # cancel or announce work through the old claim.
                    continue
                if cancel.is_set():
                    settled = jobs.cancel_running(job["id"], **terminal_kwargs)
                    if lease_token and not settled:
                        continue
                    if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id, owner):
                        webhooks.enqueue(f"job:{job['id']}:cancelled", "job.cancelled", {"job_id": job["id"], "status": "cancelled"}, principal=owner)
                else:
                    jobs.finish(job["id"], report.model_dump(), **terminal_kwargs)
                    if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id, owner):
                        webhooks.enqueue(f"job:{job['id']}:done", "job.done", {"job_id": job["id"], "status": "done", "blocked": report.blocked, "result": report.model_dump()}, principal=owner)
            except LeaseLostError:
                # Fencing refusal is not a task failure under this stale lease.
                continue
            except (OSError, ValueError, RuntimeError) as exc:
                if lease_lost.is_set():
                    continue
                try:
                    jobs.fail(job["id"], str(exc), **terminal_kwargs)
                except LeaseLostError:
                    continue
                if not purge_if_deleted(jobs, persistence.memory, job["id"], run_id, owner):
                    state = jobs.get(job["id"])["status"]
                    if state == "failed":
                        webhooks.enqueue(f"job:{job['id']}:failed", "job.failed", {"job_id": job["id"], "status": "failed", "error": str(exc)}, principal=owner)
            finally:
                stop.set(); watcher.join(timeout=1)
    finally:
        persistence.close()

from __future__ import annotations

import asyncio

from .config import Settings
from .jobs import JobStore
from .runtime import build_agent


async def work_forever(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    jobs = JobStore(settings.data_dir / "jobs.sqlite3")
    while True:
        job = jobs.claim()
        if job is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        try:
            report = await build_agent(settings).run(job["goal"])
            jobs.finish(job["id"], report.model_dump())
        except (OSError, ValueError, RuntimeError) as exc:
            jobs.fail(job["id"], str(exc))

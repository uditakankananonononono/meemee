from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from .jobs import JobStore


async def job_event_stream(
    jobs: JobStore,
    job_id: str,
    after: int = 0,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Resume-safe SSE stream backed by durable job events."""

    cursor = after
    since_heartbeat = 0.0
    while True:
        events = jobs.events(job_id, cursor)
        for event in events:
            cursor = event["sequence"]
            data = json.dumps(event, separators=(",", ":"), default=str)
            yield f"id: {cursor}\nevent: {event['kind']}\ndata: {data}\n\n"
            since_heartbeat = 0.0
        job = jobs.get(job_id)
        if job is None:
            yield 'event: error\ndata: {"detail":"job not found"}\n\n'
            return
        if job["status"] in {"done", "failed", "cancelled"} and not jobs.events(job_id, cursor):
            return
        await asyncio.sleep(poll_seconds)
        since_heartbeat += poll_seconds
        if since_heartbeat >= heartbeat_seconds:
            yield f": heartbeat {cursor}\n\n"
            since_heartbeat = 0.0

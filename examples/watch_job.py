"""Enqueue a durable job and follow its progress over SSE until it terminates.

Usage:
    export MEEMEE_BASE_URL=http://127.0.0.1:8787
    export MEEMEE_API_TOKEN=mee_...        # a token with jobs:read + jobs:write
    python examples/watch_job.py "Compare the top 5 local-LLM agent runtimes"
"""
from __future__ import annotations

import os
import sys

from meemee_client import JobEvent, MeemeeClient, MeemeeError, StreamError


def render(event: JobEvent) -> str:
    detail = f" {event.payload}" if event.payload else ""
    return f"#{event.sequence:<4} {event.kind}{detail}"


def main() -> int:
    base_url = os.environ.get("MEEMEE_BASE_URL", "http://127.0.0.1:8787")
    token = os.environ["MEEMEE_API_TOKEN"]
    goal = sys.argv[1] if len(sys.argv) > 1 else "Summarise today's arXiv cs.AI highlights"

    with MeemeeClient(base_url, auth=token) as client:
        created = client.jobs.create(goal)
        print(f"enqueued job {created.id}; following progress...")

        # stream_events resumes automatically with Last-Event-ID when the
        # connection drops, and returns when the job reaches done/failed/cancelled.
        for event in client.jobs.stream_events(created.id, on_heartbeat=lambda c: print(f"  ({c})")):
            print(render(event))

        job = client.jobs.get(created.id)
        if job.error:
            print(f"job ended with error: {job.error}")
            return 1
        print(f"job {job.id} is {job.status.value}")
        if job.result_data is not None:
            print(f"result: {job.result_data}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MeemeeError, StreamError) as exc:
        raise SystemExit(f"meemee error: {exc}")
    except KeyError as exc:
        raise SystemExit(f"missing environment variable: {exc}")

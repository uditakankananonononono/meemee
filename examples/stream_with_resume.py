"""Manual SSE resume: persist the event cursor so a restarted process loses nothing.

The SDK's stream_events already reconnects transparently inside one process.
This example shows the lower-level pattern for surviving a full process restart:
store the last delivered sequence, then resume with after=<cursor>.

Usage:
    export MEEMEE_BASE_URL=http://127.0.0.1:8787
    export MEEMEE_API_TOKEN=mee_...        # a token with jobs:read + jobs:write
    python examples/stream_with_resume.py "Research Tai-Ahom language resources"
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from meemee_client import JobEvent, MeemeeClient, MeemeeError

CURSOR_FILE = Path("/tmp/meemee-example-cursor.json")


def load_cursor(job_id: str) -> int:
    if CURSOR_FILE.exists():
        stored = json.loads(CURSOR_FILE.read_text())
        if stored.get("job_id") == job_id:
            return int(stored.get("cursor", 0))
    return 0


def save_cursor(job_id: str, cursor: int) -> None:
    CURSOR_FILE.write_text(json.dumps({"job_id": job_id, "cursor": cursor}))


def main() -> int:
    base_url = os.environ.get("MEEMEE_BASE_URL", "http://127.0.0.1:8787")
    token = os.environ["MEEMEE_API_TOKEN"]
    goal = "Research Tai-Ahom language learning resources"

    with MeemeeClient(base_url, auth=token) as client:
        created = client.jobs.create(goal)
        print(f"job {created.id} enqueued; cursor lives at {CURSOR_FILE}")

        cursor = load_cursor(created.id)
        if cursor:
            print(f"resuming from event {cursor}")

        def handle(event: JobEvent) -> None:
            print(f"#{event.sequence} {event.kind} {event.payload}")
            save_cursor(created.id, event.sequence)

        for event in client.jobs.stream_events(created.id, after=cursor):
            handle(event)

        CURSOR_FILE.unlink(missing_ok=True)
        print("stream finished; cursor file removed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeemeeError as exc:
        raise SystemExit(f"meemee error: {exc}")
    except KeyError as exc:
        raise SystemExit(f"missing environment variable: {exc}")

"""Quickstart: check server health, run a synchronous agent goal, print the report.

Usage:
    export MEEMEE_BASE_URL=http://127.0.0.1:8787
    export MEEMEE_API_TOKEN=mee_...        # a token with runs:write
    python examples/quickstart.py "List three actively maintained Python agent frameworks"
"""
from __future__ import annotations

import os
import sys

from meemee_client import MeemeeClient, MeemeeError


def main() -> int:
    base_url = os.environ.get("MEEMEE_BASE_URL", "http://127.0.0.1:8787")
    token = os.environ["MEEMEE_API_TOKEN"]
    goal = sys.argv[1] if len(sys.argv) > 1 else "Summarise what tools you have"

    with MeemeeClient(base_url, auth=token) as client:
        health = client.health()
        print(f"server {health.version} is {health.status}")

        # A synchronous run blocks until the agent loop finishes; the SDK gives
        # it a 15-minute read timeout by default. approve_writes stays False,
        # so the agent cannot write files or run mutating tools during this run.
        report = client.runs.create(goal)
        print(f"run {report.run_id} finished in {report.steps_used} steps")
        print(report.final)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeemeeError as exc:
        raise SystemExit(f"meemee error: {exc}")
    except KeyError as exc:
        raise SystemExit(f"missing environment variable: {exc}")

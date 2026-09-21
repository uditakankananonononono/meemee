from pathlib import Path

import pytest

from meemee.jobs import JobStore
from meemee.streaming import job_event_stream


@pytest.mark.asyncio
async def test_stream_replays_and_stops_at_terminal(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work")
    jobs.claim(); jobs.finish(ident, {"final":"done"})
    chunks = [chunk async for chunk in job_event_stream(jobs, ident, poll_seconds=0)]
    joined = "".join(chunks)
    assert "event: queued" in joined
    assert "event: running" in joined
    assert "event: done" in joined


@pytest.mark.asyncio
async def test_stream_resumes_after_cursor(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work", max_attempts=1)
    first = jobs.events(ident)[0]["sequence"]
    jobs.claim(); jobs.fail(ident, "bad")
    chunks = [chunk async for chunk in job_event_stream(jobs, ident, after=first, poll_seconds=0)]
    assert "event: queued" not in "".join(chunks)
    assert "event: running" in "".join(chunks)


@pytest.mark.asyncio
async def test_stream_stops_after_cancelled(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work")
    jobs.request_cancel(ident)
    chunks = [chunk async for chunk in job_event_stream(jobs, ident, poll_seconds=0)]
    assert "event: cancelled" in "".join(chunks)

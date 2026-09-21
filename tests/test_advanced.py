from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meemee.jobs import JobStore
from meemee.tools.git import GitCommit
from meemee.tools.shell import ShellArgs, ShellCommand


@pytest.mark.asyncio
async def test_shell_executes_without_shell(tmp_path: Path):
    tool = ShellCommand(tmp_path, {"python3"})
    result = await tool.run(ShellArgs(argv=["python3", "-c", "print('ok')"]))
    assert result["exit_code"] == 0 and result["stdout"] == "ok\n"


@pytest.mark.asyncio
async def test_shell_rejects_non_allowlisted(tmp_path: Path):
    with pytest.raises(ValueError, match="not allowlisted"):
        await ShellCommand(tmp_path, {"python3"}).run(ShellArgs(argv=["sh", "-c", "echo no"]))


def test_job_queue_due_and_future(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    future = store.enqueue("later", datetime.now(timezone.utc) + timedelta(hours=1))
    due = store.enqueue("now")
    claimed = store.claim()
    assert claimed and claimed["id"] == due
    store.finish(due, {"final": "done"})
    assert store.get(due)["status"] == "done"
    assert store.get(future)["status"] == "queued"


def test_job_retries_then_fails(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    ident = store.enqueue("fail", max_attempts=1)
    assert store.claim()["id"] == ident
    store.fail(ident, "bad")
    assert store.get(ident)["status"] == "failed"


def test_git_path_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        GitCommit(tmp_path).safe_path("../outside")


def test_cancel_queued_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    ident = store.enqueue("cancel me")
    assert store.cancel(ident)
    assert store.get(ident)["error"] == "cancelled"
    assert not store.cancel(ident)


def test_job_event_stream(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    ident = store.enqueue("work")
    store.claim()
    store.finish(ident, {"final": "ok"})
    assert [event["kind"] for event in store.events(ident)] == ["queued", "running", "done"]
    first = store.events(ident)[0]["sequence"]
    assert [event["kind"] for event in store.events(ident, first)] == ["running", "done"]

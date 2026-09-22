from datetime import datetime, timedelta, timezone
from pathlib import Path

from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.retention import RetentionManager


def test_retention_removes_old_terminal_and_memory_but_not_active(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.sqlite3")
    old = jobs.enqueue("old"); jobs.claim(); jobs.finish(old, {"done":True})
    active = jobs.enqueue("active")
    memory = MemoryStore(tmp_path / "meemee.sqlite3"); memory.add("r","fact","old memory")
    past = (datetime.now(timezone.utc)-timedelta(days=100)).isoformat()
    with jobs.db: jobs.db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (past, old))
    with memory.connection: memory.connection.execute("UPDATE memories SET created_at=?", (past,))
    report = RetentionManager(tmp_path).run(jobs_days=30, memory_days=30)
    assert report.terminal_jobs == 1 and report.job_events == 3 and report.memories == 1
    assert jobs.get(active)["status"] == "queued"


def test_retention_rejects_zero_window(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError): RetentionManager(tmp_path).run(jobs_days=0)


def test_retention_removes_old_run_history(tmp_path: Path):
    from meemee.runs import RunStore
    from meemee.types import RunReport
    store=RunStore(tmp_path/"runs.sqlite3")
    store.add("u",RunReport(run_id="r",goal="g",final="f",steps_used=1,tool_results=[]))
    past=(datetime.now(timezone.utc)-timedelta(days=100)).isoformat()
    store.db.execute("UPDATE runs SET created_at=?",(past,)); store.db.commit()
    report=RetentionManager(tmp_path).run(runs_days=30)
    assert report.runs==1 and store.list("u")[0]==[]

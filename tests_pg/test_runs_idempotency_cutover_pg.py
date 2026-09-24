"""Run history and idempotency records move to PostgreSQL with the cutover CLI."""
from __future__ import annotations

import json
import os
import uuid

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_runs_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_runs_and_idempotency_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.idempotency import IdempotencyInProgress, IdempotencyStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee.runs import RunStore
    from meemee.types import ApprovalRefusal, RunReport
    from meemee_persist_pg import Database
    from meemee_persist_pg import IdempotencyStore as PGIdempotency
    from meemee_persist_pg import RunStore as PGRuns
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    runs = RunStore(tmp_path / "runs.sqlite3")
    blocked = ApprovalRefusal(step=1, tool="github.create_issue", risk="write", reason="approval_required",
                              detail="needs approval", arguments={"repo": "a/b"}, grantable=True)
    runs.add("alice", RunReport(run_id="r1", goal="read", final="done", steps_used=2,
                                tool_results=[{"tool": "workspace.read_file", "result": {"ok": True, "text": "héllo"}}]))
    runs.add("alice", RunReport(run_id="r2", goal="write", final="blocked", steps_used=1, tool_results=[],
                                approvals_required=[blocked]))
    keys = IdempotencyStore(tmp_path / "idempotency.sqlite3")
    keys.put("alice", "/v1/jobs", "done", {"goal": "x"}, 200, {"id": "job-1", "quota": {"used": 1}})
    assert keys.claim("alice", "/v1/jobs", "open", {"goal": "y"}) is None  # in flight at cutover time

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--runs", str(tmp_path / "runs.sqlite3"),
            "--idempotency", str(tmp_path / "idempotency.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_runs"] == 2 and report["copied"]["meemee_idempotency"] == 2
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg_runs, pg_keys = PGRuns(db), PGIdempotency(db)
        assert pg_runs.get("alice", "r1") == runs.get("alice", "r1")
        assert pg_runs.get("alice", "r2") == runs.get("alice", "r2") and pg_runs.get("bob", "r1") is None
        assert [r["run_id"] for r in pg_runs.list("alice")[0]] == [r["run_id"] for r in runs.list("alice")[0]]
        assert pg_keys.claim("alice", "/v1/jobs", "done", {"goal": "x"}) == (200, {"id": "job-1", "quota": {"used": 1}})
        with pytest.raises(IdempotencyInProgress):
            pg_keys.claim("alice", "/v1/jobs", "open", {"goal": "y"})
    finally:
        db.close()

"""The account-deletion ledger moves to PostgreSQL with the cutover CLI."""
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

    name = "meemee_del_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_deletion_ledger_survives_cutover(dsn, tmp_path, capsys):
    from meemee.account_deletion import DeletionLedger
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import Database
    from meemee_persist_pg import DeletionLedger as PGLedger
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    local = DeletionLedger(tmp_path / "account-deletions.sqlite3")
    done = local.open("p1", "admin"); local.record_step(done, "jobs", {"jobs": 2}); local.complete(done)
    open_one = local.open("p2", "admin"); local.record_step(open_one, "jobs", {"jobs": 1})

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--deletions", str(tmp_path / "account-deletions.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_account_deletions"] == 2 and report["copied"]["meemee_account_deletion_steps"] == 2
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg = PGLedger(db)
        assert pg.get(done) == local.get(done) and pg.incomplete() == local.incomplete()
        assert pg.open("p2", "admin") == open_one  # the interrupted deletion resumes, not a second one
    finally:
        db.close()

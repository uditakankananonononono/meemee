"""Pending and used email/reset challenges move to PostgreSQL with the cutover CLI (optional `email` group)."""
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

    name = "meemee_email_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_email_challenges_survive_cutover_and_stay_one_time(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.email_verification import EmailVerificationStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import Database
    from meemee_persist_pg import EmailVerificationStore as PGStore
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3")
    tokens = TokenStore(tmp_path / "auth.sqlite3")
    pending, _ = tokens.create_account("pending@example.com", "correct horse battery", "P")
    done, _ = tokens.create_account("done@example.com", "correct horse battery", "D")
    email = EmailVerificationStore(tmp_path / "email-verifications.sqlite3")
    pending_raw = email.issue(pending["id"])
    done_raw = email.issue(done["id"]); assert email.verify(done["id"], done_raw)
    reset_raw = email.issue_password_reset(pending["id"])

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--email", str(tmp_path / "email-verifications.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_email_verifications"] == 2 and report["copied"]["meemee_password_resets"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg = PGStore(db)
        assert pg.status(done["id"]) is True and pg.status(pending["id"]) is False
        assert not pg.verify(done["id"], done_raw)  # already used before cutover
        assert pg.verify(pending["id"], pending_raw) and pg.status(pending["id"])  # link sent before cutover still works
        assert pg.consume_password_reset(pending["id"], reset_raw)
        assert not pg.consume_password_reset(pending["id"], reset_raw)
    finally:
        db.close()

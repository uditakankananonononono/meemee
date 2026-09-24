"""Quota limits, today's usage and plan assignments move to PostgreSQL with the cutover CLI."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_qe_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_quotas_and_plans_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.entitlements import EntitlementStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee.quotas import QuotaExceeded, QuotaStore
    from meemee_persist_pg import Database
    from meemee_persist_pg import EntitlementStore as PGEntitlements
    from meemee_persist_pg import QuotaStore as PGQuotas
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    now = datetime.now(timezone.utc)
    quotas = QuotaStore(tmp_path / "quotas.sqlite3")
    quotas.set_limit("capped", 2); quotas.consume_job("capped", now)
    quotas.consume_job("free", now)
    plans = EntitlementStore(tmp_path / "entitlements.sqlite3")
    plans.assign("capped", "team", now.isoformat())

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--quotas", str(tmp_path / "quotas.sqlite3"),
            "--entitlements", str(tmp_path / "entitlements.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_quota_limits"] == 1 and report["copied"]["meemee_quota_usage"] == 2
    assert report["copied"]["meemee_principal_plans"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        q, e = PGQuotas(db), PGEntitlements(db)
        assert q.status("capped", now)["used"] == 1 and q.limit("capped") == 2
        q.consume_job("capped", now)  # today's usage carried over: one left, then refused
        with pytest.raises(QuotaExceeded):
            q.consume_job("capped", now)
        assert e.plan_name("capped") == "team" and e.plan_name("free") == "starter"
    finally:
        db.close()

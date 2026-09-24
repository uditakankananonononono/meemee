"""The secret vault moves to PostgreSQL with the cutover CLI; secrets stay readable with the same key."""
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

    name = "meemee_vault_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_vault_survives_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee.vault import SecretVault
    from meemee_persist_pg import Database
    from meemee_persist_pg import SecretVault as PGVault
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    key = SecretVault.generate_key()
    local = SecretVault(tmp_path / "vault.sqlite3", key)
    local.put("api", "s3cret"); local.put("mail", "p4ss")
    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--vault", str(tmp_path / "vault.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_vault_secrets"] == 2
    assert all(item["match"] for item in report["verification"].values()), report["verification"]
    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg = PGVault(db, key)
        assert pg.names() == ["api", "mail"] and pg.get("api") == "s3cret" and pg.get("mail") == "p4ss"
    finally:
        db.close()

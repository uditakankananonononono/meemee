"""Moving existing SQLite tool grants into PostgreSQL with the offline cutover tool."""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_grant_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _sources(root):
    from meemee.approvals import ApprovalStore
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore

    MemoryStore(root / "meemee.sqlite3"); PlanStore(root / "plans.sqlite3"); JobStore(root / "jobs.sqlite3")
    TokenStore(root / "auth.sqlite3"); AuditLog(root / "audit.sqlite3")
    grants = ApprovalStore(root / "approvals.sqlite3")
    grants.grant("alice", "workspace.write_file", "api-admin", "2031-01-01T00:00:00Z", {"path": "notes/a.txt"})
    grants.grant("alice", "git.commit", "api-admin"); grants.revoke("alice", "git.commit")
    grants.grant("bob", "shell.command", "api-admin", argument_constraints={"command": "pytest"})
    grants.db.close()
    return {"memory": root / "meemee.sqlite3", "plans": root / "plans.sqlite3", "jobs": root / "jobs.sqlite3",
            "tokens": root / "auth.sqlite3", "audit": root / "audit.sqlite3"}, root / "approvals.sqlite3"


def test_cli_copies_and_verifies_grants_then_pg_store_enforces_them(dsn, tmp_path, capsys):
    import json

    from meemee_persist_pg import ApprovalStore, Database
    from meemee_persist_pg.cli import main

    sources, approvals = _sources(tmp_path)
    args = ["--dsn", dsn] + [a for name, path in sources.items() for a in (f"--{name}", str(path))]
    assert main(args + ["--approvals", str(approvals), "copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_tool_approvals"] == 3
    assert report["verification"]["meemee_tool_approvals"]["match"] is True
    assert main(args + ["--approvals", str(approvals), "verify"]) == 0

    database = Database(dsn, min_size=1, max_size=2)
    try:
        store = ApprovalStore(database)
        assert store.allows("alice", "workspace.write_file", arguments={"path": "notes/a.txt"})
        assert not store.allows("alice", "workspace.write_file", arguments={"path": "elsewhere"})
        assert not store.allows("alice", "git.commit")
        assert store.allows("bob", "shell.command", arguments={"command": "pytest"})
        assert store.active_count("alice") == 1
    finally:
        database.close()


def test_cutover_without_approvals_source_still_works(dsn, tmp_path):
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.cutover import Cutover

    sources, _ = _sources(tmp_path)
    database = Database(dsn, min_size=1, max_size=2)
    try:
        MigrationStore(database).apply()
        cutover = Cutover(database, sources)
        copied = cutover.copy()
        assert "meemee_tool_approvals" not in copied
        assert all(item["match"] for item in cutover.verify().values())
        with pytest.raises(ValueError, match="missing SQLite sources"):
            Cutover(database, {k: v for k, v in sources.items() if k != "jobs"})
    finally:
        database.close()


def test_verify_detects_a_grant_changed_after_copy(dsn, tmp_path):
    from meemee.approvals import ApprovalStore as SQLiteApprovalStore
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.cutover import Cutover

    sources, approvals = _sources(tmp_path)
    database = Database(dsn, min_size=1, max_size=2)
    try:
        MigrationStore(database).apply()
        cutover = Cutover(database, {**sources, "approvals": approvals})
        cutover.copy()
        SQLiteApprovalStore(approvals).revoke("bob", "shell.command")
        assert cutover.verify()["meemee_tool_approvals"]["match"] is False
    finally:
        database.close()


def test_account_purger_in_postgresql_mode_deletes_shared_grants(dsn, tmp_path):
    from types import SimpleNamespace

    from meemee.account_deletion import build_account_purger
    from meemee.persistence import build_persistence

    persistence = build_persistence("postgresql", tmp_path, dsn)
    try:
        persistence.approvals.grant("alice", "git.commit", "api-admin")
        persistence.approvals.grant("bob", "git.commit", "api-admin")
        settings = SimpleNamespace(data_dir=tmp_path, default_daily_jobs=10, default_plan="starter",
                                   webhook_max_payload_bytes=65536, vault_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        purger = build_account_purger(settings, persistence)
        assert purger.targets.approvals is persistence.approvals
        purger.purge("alice", requested_by="alice")
        assert persistence.approvals.list("alice") == []
        assert persistence.approvals.allows("bob", "git.commit")
        assert not (tmp_path / "approvals.sqlite3").exists()
    finally:
        persistence.close()

"""Moving SQLite tokens, accounts and a (pruned) audit chain into PostgreSQL with the cutover CLI."""
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

    name = "meemee_tok_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_tokens_accounts_and_pruned_audit_chain_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog as SQLiteAudit
    from meemee.auth import TokenStore as SQLiteTokens
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import AuditLog, Database, TokenStore
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    tokens = SQLiteTokens(tmp_path / "auth.sqlite3")
    admin_id, admin_raw = tokens.create("ops", {"admin"})
    key_id, key_raw = tokens.create("key", {"jobs:read"}, "2999-01-01T00:00:00Z", owner_id="acct_legacy")
    revoked_id, revoked_raw = tokens.create("gone", {"jobs:read"}); tokens.revoke(revoked_id)
    account, session = tokens.create_account("old@example.com", "correct horse battery", "Old")
    tokens.db.close()

    audit = SQLiteAudit(tmp_path / "audit.sqlite3")
    for i in range(6):
        audit.append("ops", "write", f"r{i}", "success", {"i": i, "note": "caf\u00e9"})
    # Simulate a pruned chain: drop the first three rows and record the base, as audit-prune does.
    with audit.db:
        base = audit.db.execute("SELECT sequence,entry_hash FROM audit_log WHERE sequence=3").fetchone()
        audit.db.execute("DELETE FROM audit_log WHERE sequence<=3")
        audit.db.execute("INSERT INTO audit_chain_base(singleton,sequence,entry_hash) VALUES(1,?,?)", (base[0], base[1]))
    assert audit.verify() == (True, None)
    audit.db.close()

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_api_tokens"] == 4 and report["copied"]["meemee_accounts"] == 1
    assert report["copied"]["meemee_audit_log"] == 3 and report["copied"]["meemee_audit_chain_base"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg_tokens, pg_audit = TokenStore(db), AuditLog(db)
        assert pg_tokens.authenticate(admin_raw).id == admin_id
        assert pg_tokens.authenticate(key_raw).id == "acct_legacy"
        assert pg_tokens.authenticate(revoked_raw) is None
        assert pg_tokens.authenticate(session).id == account["id"]
        assert pg_tokens.login_account("old@example.com", "correct horse battery")[0]["id"] == account["id"]
        assert pg_tokens.introspect(key_raw)["expires_at"].startswith("2999-01-01T00:00:00")
        assert key_id in {t["id"] for t in pg_tokens.list_metadata(owner_id="acct_legacy")[0]}

        assert pg_audit.verify() == (True, None)
        new_seq = pg_audit.append("pg", "write", "after-cutover", "success", {"i": 99})
        assert new_seq == 7
        entries = pg_audit.list()
        assert [e["sequence"] for e in entries] == [4, 5, 6, 7] and entries[0]["previous_hash"] == base[1]
        assert pg_audit.verify() == (True, None)
    finally:
        db.close()

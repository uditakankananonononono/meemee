"""PostgreSQL operator tools (item 187): export/import, retention and signed audit anchors.

Covers portability of ``meemee.account.v1`` exports between backends, one-transaction imports,
retention on the shared tables, anchor tamper detection, and a SQLite anchor that still verifies
and prunes after the chain is cut over to PostgreSQL.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")
KEY = "operator-tools-anchor-key-0123456789abcdef"


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_ops_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def db(dsn):
    from meemee_persist_pg import Database, MigrationStore

    database = Database(dsn); MigrationStore(database).apply()
    yield database
    database.close()


def _strip(payload):
    """Rows without the owner; job IDs compared as UUIDs (SQLite stores hex, PostgreSQL the dashed
    form) and JSON columns compared decoded."""
    def clean(row):
        row = {c: (json.loads(v) if c in ("result", "tool_results", "approvals_required") and v else v)
               for c, v in row.items() if c != "principal"}  # jsonb re-orders keys; compare values
        return {**row, "id": uuid.UUID(row["id"]).hex} if "id" in row else row
    return {k: [clean(row) for row in payload[k]] for k in ("jobs", "runs", "entitlements")}


def test_export_moves_between_backends_without_loss(db, tmp_path):
    from meemee.account_export import export_account as export_sqlite
    from meemee.account_export import import_account as import_sqlite
    from meemee.entitlements import EntitlementStore
    from meemee.jobs import JobStore
    from meemee.runs import RunStore
    from meemee.types import ApprovalRefusal, RunReport
    from meemee_persist_pg.operator_tools import export_account, import_account

    src = tmp_path / "src"; src.mkdir()
    jobs = JobStore(src / "jobs.sqlite3"); runs = RunStore(src / "runs.sqlite3")
    EntitlementStore(src / "entitlements.sqlite3", "starter").assign("alice", "team", "2026-09-24T12:00:00+00:00")
    queued = jobs.enqueue("later", datetime(2999, 1, 1, tzinfo=timezone.utc), principal="alice")
    done = jobs.enqueue("now", principal="alice"); jobs.claim(); jobs.finish(done, {"run_id": "r1", "text": "héllo"})
    refusal = ApprovalRefusal(step=1, tool="github.create_issue", risk="write", reason="approval_required",
                              detail="needs approval", arguments={"repo": "a/b"}, grantable=True)
    runs.add("alice", RunReport(run_id="r1", goal="read", final="done", steps_used=2,
                                tool_results=[{"tool": "workspace.read_file", "result": {"ok": True, "text": "héllo"}}]))
    runs.add("alice", RunReport(run_id="r2", goal="write", final="blocked", steps_used=1, tool_results=[],
                                approvals_required=[refusal]))
    jobs.db.close(); runs.db.close()
    sqlite_export = tmp_path / "sqlite.json"
    export_sqlite(src, "alice", sqlite_export)

    assert import_account(db, sqlite_export, "bob")["jobs"] == 2
    pg_export = tmp_path / "pg.json"
    report = export_account(db, "bob", pg_export)
    assert (report["jobs"], report["runs"], report["entitlements"]) == (2, 2, 1)
    left = json.loads(sqlite_export.read_text())["payload"]; right = json.loads(pg_export.read_text())["payload"]
    assert {uuid.UUID(r["id"]).hex for r in right["jobs"]} == {queued, done}
    assert _strip(left) == _strip(right)  # identical field encodings: timestamps, JSON text, 0/1 flags

    # ...and the PostgreSQL export restores into a SQLite data directory.
    dst = tmp_path / "dst"; dst.mkdir()
    JobStore(dst / "jobs.sqlite3").db.close(); RunStore(dst / "runs.sqlite3").db.close()
    EntitlementStore(dst / "entitlements.sqlite3", "starter")
    import_sqlite(dst, pg_export, "carol")
    back = tmp_path / "back.json"; export_sqlite(dst, "carol", back)
    assert _strip(json.loads(back.read_text())["payload"]) == _strip(left)
    assert RunStore(dst / "runs.sqlite3").get("carol", "r2")["approvals_required"][0]["tool"] == "github.create_issue"


def test_import_is_one_transaction(db, tmp_path):
    from meemee_persist_pg import EntitlementStore, JobStore
    from meemee_persist_pg.operator_tools import export_account, import_account

    JobStore(db).enqueue("x", principal="alice")
    EntitlementStore(db, "starter").assign("alice", "team", "2026-09-24T12:00:00+00:00")
    out = tmp_path / "a.json"; export_account(db, "alice", out)
    with db.transaction() as c:
        c.execute("DELETE FROM meemee_jobs")  # job IDs free, so only the entitlement collides
    EntitlementStore(db, "starter").assign("zed", "business", "2026-09-24T12:00:00+00:00")
    with pytest.raises(ValueError, match="entitlement"):
        import_account(db, out, "zed")
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM meemee_jobs").fetchone()["n"] == 0
    assert import_account(db, out, "alice2")["jobs"] == 1
    with pytest.raises(ValueError, match="job ID collision"):
        import_account(db, out, "alice3")


def test_retention_prunes_each_shared_table_and_keeps_the_audit_chain(db):
    from meemee_persist_pg import AuditLog, JobStore
    from meemee_persist_pg.operator_tools import run_retention

    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc); old = now - timedelta(days=400)
    audit = AuditLog(db); audit.append("ops", "x", "r", "success")
    store = JobStore(db)
    old_done, new_done, old_queued = store.enqueue("a", principal="p"), store.enqueue("b", principal="p"), store.enqueue("c", principal="p")
    with db.transaction() as c:
        c.execute("UPDATE meemee_jobs SET status='done' WHERE id IN (%s,%s)", (old_done, new_done))
        c.execute("UPDATE meemee_jobs SET updated_at=%s WHERE id IN (%s,%s)", (old, old_done, old_queued))
        c.execute("UPDATE meemee_jobs SET updated_at=%s WHERE id=%s", (now, new_done))
        c.execute("INSERT INTO meemee_memories(run_id,kind,content,created_at) VALUES('r','note','old',%s),('r','note','new',%s)", (old, now))
        c.execute("""INSERT INTO meemee_runs(run_id,principal,goal,final,steps_used,tool_results,created_at)
                     VALUES('old','p','g','f',1,'[]',%s),('new','p','g','f',1,'[]',%s)""", (old, now))
        c.execute("""INSERT INTO meemee_idempotency(principal,route,key,request_hash,response,status,created_at,expires_at)
                     VALUES('p','/v1/jobs','gone',%s,'{}',200,%s,%s),('p','/v1/jobs','live',%s,'{}',200,%s,%s)""",
                  ("0" * 64, old, now - timedelta(seconds=1), "0" * 64, now, now + timedelta(hours=1)))
        for ident, status in (("d-old", "delivered"), ("f-old", "failed"), ("q-old", "queued")):
            c.execute("""INSERT INTO meemee_webhook_deliveries(id,subscription_id,event_id,event_type,payload,status,next_attempt_at,created_at)
                         VALUES(%s,'s',%s,'job.done','{}',%s,0,%s)""", (ident, ident, status, old))
            c.execute("INSERT INTO meemee_webhook_attempts(delivery_id,attempt,started_at) VALUES(%s,1,%s)", (ident, old))
    report = run_retention(db, now=now)
    assert (report.terminal_jobs, report.memories, report.runs, report.idempotency) == (1, 1, 1, 1)
    assert (report.webhook_delivered, report.webhook_failed, report.audit_entries) == (1, 1, 0)
    assert report.job_events >= 1
    with db.transaction() as c:
        assert {str(r["id"]) for r in c.execute("SELECT id FROM meemee_jobs")} == {new_done, old_queued}
        assert [r["delivery_id"] for r in c.execute("SELECT delivery_id FROM meemee_webhook_attempts")] == ["q-old"]
        assert [r["key"] for r in c.execute("SELECT key FROM meemee_idempotency")] == ["live"]
    assert audit.verify() == (True, None) and len(audit.list()) == 1
    with pytest.raises(ValueError):
        run_retention(db, now=now, runs_days=0)


def test_anchor_detects_tampering_and_refuses_stale_prunes(db, tmp_path):
    from meemee_persist_pg import AuditLog
    from meemee_persist_pg.operator_tools import create_anchor, prune_to_anchor, verify_anchor

    audit = AuditLog(db)
    with pytest.raises(ValueError, match="32 bytes"):
        create_anchor(db, tmp_path / "short.json", "short")
    empty = create_anchor(db, tmp_path / "empty.json", KEY)
    assert empty["sequence"] == 0 and verify_anchor(db, tmp_path / "empty.json", KEY)["status"] == "pass"
    with pytest.raises(ValueError, match="empty-chain"):
        prune_to_anchor(db, tmp_path / "empty.json", KEY)
    for i in range(5):
        audit.append("ops", "write", f"r{i}", "success", {"i": i})
    first = create_anchor(db, tmp_path / "first.json", KEY)
    with pytest.raises(FileExistsError):
        create_anchor(db, tmp_path / "first.json", KEY)
    assert verify_anchor(db, tmp_path / "first.json", "x" * 40)["signature_valid"] is False
    for i in range(3):
        audit.append("ops", "write", f"s{i}", "success", {"i": i})
    second = create_anchor(db, tmp_path / "second.json", KEY)
    assert prune_to_anchor(db, tmp_path / "second.json", KEY)["deleted_entries"] == 8
    with pytest.raises(ValueError):  # older anchor: its entry is gone and it predates the base
        prune_to_anchor(db, tmp_path / "first.json", KEY)
    assert verify_anchor(db, tmp_path / "second.json", KEY)["status"] == "pass"
    head = create_anchor(db, tmp_path / "head.json", KEY)  # fully pruned chain: the base is the head
    assert (head["sequence"], head["entry_hash"]) == (second["sequence"], second["entry_hash"])
    audit.append("ops", "write", "t", "success", {"i": 1})
    assert audit.verify() == (True, None)

    with db.transaction() as c:  # a superuser edit breaks the chain; anchors refuse to certify it
        c.execute("UPDATE meemee_audit_log SET metadata='{\"i\":2}'::jsonb WHERE resource='t'")
    with pytest.raises(ValueError, match="invalid"):
        create_anchor(db, tmp_path / "tampered.json", KEY)
    report = verify_anchor(db, tmp_path / "second.json", KEY)
    assert report["status"] == "fail" and report["chain_valid"] is False
    assert first["sequence"] < second["sequence"]


def test_sqlite_anchor_still_verifies_and_prunes_after_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog as SQLiteAudit
    from meemee.audit_anchor import create_anchor as sqlite_anchor
    from meemee.audit_anchor import prune_to_anchor as sqlite_prune
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import AuditLog, Database
    from meemee_persist_pg.cli import main
    from meemee_persist_pg.operator_tools import create_anchor, prune_to_anchor, verify_anchor

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    TokenStore(tmp_path / "auth.sqlite3")
    audit = SQLiteAudit(tmp_path / "audit.sqlite3")
    for i in range(6):
        audit.append("ops", "write", f"r{i}", "success", {"i": i, "note": "café"})
    sqlite_anchor(audit, tmp_path / "early.json", KEY)
    for i in range(4):
        audit.append("ops", "write", f"s{i}", "success", {"i": i})
    sqlite_prune(audit, tmp_path / "early.json", KEY)  # the SQLite chain already has a base
    sqlite_anchor(audit, tmp_path / "late.json", KEY)
    audit.db.close()

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3")]
    assert main(args + ["copy"]) == 0
    capsys.readouterr()
    db = Database(dsn)
    try:
        assert verify_anchor(db, tmp_path / "early.json", KEY)["status"] == "pass"  # now the chain base
        assert verify_anchor(db, tmp_path / "late.json", KEY)["status"] == "pass"
        AuditLog(db).append("pg", "write", "after", "success", {"i": 99})
        assert prune_to_anchor(db, tmp_path / "late.json", KEY)["deleted_entries"] == 4
        assert AuditLog(db).verify() == (True, None)
        anchor = create_anchor(db, tmp_path / "pg.json", KEY)
        assert anchor["sequence"] == json.loads((tmp_path / "late.json").read_text())["sequence"] + 1
    finally:
        db.close()

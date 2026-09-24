"""Webhook subscriptions, the outbox and attempt history move to PostgreSQL with the cutover CLI."""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")
KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_wh_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_webhooks_survive_cutover_with_secrets_payload_bytes_and_history(dsn, tmp_path, capsys, monkeypatch):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee.webhooks import WebhookDispatcher, WebhookStore
    from meemee_persist_pg import Database
    from meemee_persist_pg import WebhookStore as PGStore
    from meemee_persist_pg.cli import main

    monkeypatch.setattr("meemee.webhooks.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))])
    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    local = WebhookStore(tmp_path / "webhooks.sqlite3", encryption_key=KEY)
    live, secret = local.subscribe("alice", "https://hooks.example/a", {"job.done"}, headers={"X-Team": "ops"})
    paused, _ = local.subscribe("alice", "https://hooks.example/b", {"*"})
    local.set_active(paused, "alice", False)
    local.enqueue("job:1:done", "job.done", {"job_id": "1", "note": "héllo"}, principal="alice")
    local.enqueue("job:2:done", "job.done", {"job_id": "2"}, principal="alice")
    done = local.claim(now=time.time() + 1); local.succeed(done["id"], 200)
    pending = local.list_deliveries("alice", status="queued")[0][0]
    original_payload = local.claim(now=time.time() + 1)["payload"]; local.retry(pending["id"], "HTTP 503")

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--webhooks", str(tmp_path / "webhooks.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_webhook_subscriptions"] == 2 and report["copied"]["meemee_webhook_deliveries"] == 2
    assert report["copied"]["meemee_webhook_attempts"] == 2
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg = PGStore(db, encryption_key=KEY)
        assert pg.list_subscriptions("alice")[0] == local.list_subscriptions("alice")[0]
        assert pg.active_count("alice") == 1 and pg.health(live, "alice") == local.health(live, "alice")
        assert pg.list_deliveries("alice")[0] == local.list_deliveries("alice")[0]
        assert pg.attempt_timeline(done["id"], "alice") == local.attempt_timeline(done["id"], "alice")
        retried = pg.claim(now=time.time() + 3600)  # the retry backoff carried over
        assert retried["id"] == pending["id"] and retried["payload"] == original_payload and retried["secret"] == secret
        headers = json.loads(retried["headers"])
        assert headers == {"X-Team": "ops"}
        timestamp = "1700000000"
        assert WebhookDispatcher.signature(retried["secret"], timestamp, retried["payload"]) == \
            WebhookDispatcher.signature(secret, timestamp, original_payload)
        pg.retry(retried["id"], "HTTP 500")  # attempt ids keep counting after the copied rows
        assert [a["attempt"] for a in pg.attempt_timeline(pending["id"], "alice")] == [1, 2]
    finally:
        db.close()

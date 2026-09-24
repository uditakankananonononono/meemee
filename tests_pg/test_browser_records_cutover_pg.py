"""Browser session records and takeover notices move to PostgreSQL with the cutover CLI."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_br_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_browser_records_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.browser_notices import TakeoverNoticeQueue
    from meemee.browser_sessions import BrowserSessionStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import BrowserSessionStore as PGSessions
    from meemee_persist_pg import Database
    from meemee_persist_pg import TakeoverNoticeQueue as PGNotices
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    sessions = BrowserSessionStore(tmp_path / "browser-sessions.sqlite3", "host-a")
    sessions.create_session("s1", "ana", None, ["example.com"]); sessions.event("s1", "agent", "opened", {})
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    sessions.create_takeover("t1", "s1", "tok", "captcha", "agent", expires)
    notices = TakeoverNoticeQueue(tmp_path / "browser-notices.sqlite3")
    notices.enqueue({"takeover_id": "t1", "reason": "captcha", "expires_at": expires.isoformat(), "url": "http://h/x"}, "s1", "ana")

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--browser", str(tmp_path / "browser-sessions.sqlite3"),
            "--notices", str(tmp_path / "browser-notices.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_browser_sessions"] == 1 and report["copied"]["meemee_browser_takeovers"] == 1
    assert report["copied"]["meemee_browser_session_events"] == 1 and report["copied"]["meemee_browser_takeover_notices"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg, pgn = PGSessions(db, "host-a"), PGNotices(db)
        assert pg.get_session("s1") == sessions.get_session("s1") and pg.get_takeover("t1") == sessions.get_takeover("t1")
        assert pg.events("s1") == sessions.events("s1") and pgn.list() == notices.list()
        pg.event("s1", "agent", "after", {})
        assert [e["id"] for e in pg.events("s1")] == [1, 2]
    finally:
        db.close()

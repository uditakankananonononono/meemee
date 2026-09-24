"""Browser session records and takeover notices: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.browser_notices import TakeoverNoticeQueue as SQLiteNotices
from meemee.browser_sessions import BrowserSessionStore as SQLiteSessions


@pytest.fixture(params=BACKENDS)
def make(request, tmp_path):
    if request.param == "sqlite":
        yield lambda host: (SQLiteSessions(tmp_path / "s.sqlite3", host), SQLiteNotices(tmp_path / "n.sqlite3"))
        return
    from meemee_persist_pg import BrowserSessionStore, Database, MigrationStore, TakeoverNoticeQueue

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield lambda host: (BrowserSessionStore(db, host), TakeoverNoticeQueue(db))
    finally:
        db.close(); drop()


def test_session_records_and_host_scoped_restart(make):
    a, _ = make("host-a")
    b, _ = make("host-b")
    assert a.ping()
    a.create_session("s1", "o", None, ["example.com"])
    b.create_session("s2", "o", "p", [])
    a.update_session("s1", url="https://example.com", title="Ex")
    with pytest.raises(ValueError):
        a.update_session("s1", owner_id="x")
    a.event("s1", "agent", "opened", {"u": 1}); a.event("s1", "agent", "nav", {})
    assert [e["kind"] for e in a.events("s1")] == ["opened", "nav"] and a.events("s1")[0]["detail"] == {"u": 1}
    s1 = a.get_session("s1")
    assert s1["allowed_domains"] == ["example.com"] and s1["host_id"] == "host-a" and s1["title"] == "Ex"
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    a.create_takeover("t1", "s1", "tok", "captcha", "agent", expires)
    b.create_takeover("t2", "s2", "tok2", "login", "agent", expires)
    assert bytes(a.get_takeover("t1")["token_digest"]) == bytes(b.get_takeover("t1")["token_digest"])
    a.claim_takeover("t1")
    assert a.get_takeover("t1")["claimed_at"] and [t["id"] for t in a.session_takeovers("s1")] == ["t1"]
    # a restart of host B marks only host B's open sessions and takeovers lost
    assert b.mark_open_sessions_lost() == 1
    assert a.get_session("s1")["state"] == "agent" and a.get_session("s2")["state"] == "lost"
    assert a.get_takeover("t1")["finished_at"] is None and a.get_takeover("t2")["outcome"] == "lost"
    assert a.finish_takeover("t1", "completed", "ok") and not a.finish_takeover("t1", "completed")
    assert {s["id"] for s in a.list_sessions(None)} == {"s1", "s2"} and a.list_sessions("x") == []
    assert a.delete_owner("o") == {"browser_sessions": 2, "browser_takeovers": 2, "browser_events": 2}


def test_notice_queue_and_delivery(make):
    sessions, notices = make("host-a")
    assert notices.ping()
    sessions.create_session("s1", "o", None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    sessions.create_takeover("t1", "s1", "tok", "captcha", "agent", expires)
    sessions.create_takeover("t2", "s1", "tok", "gone", "agent", expires)
    sessions.finish_takeover("t2", "completed")
    take = lambda t: {"takeover_id": t, "reason": "captcha", "expires_at": expires.isoformat(), "url": "http://h/x"}
    notices.enqueue(take("t1"), "s1", "ana"); notices.enqueue(take("t1"), "s1", "ana"); notices.enqueue(take("t2"), "s1", "ana")
    assert len(notices.list("s1")) == 2

    class Profile:
        class checkins:
            channel, address = "fake", "addr"

    class Companion:
        def profile(self, user):
            return Profile()

    class Result:
        detail = "sent"

    sent = []

    class Channel:
        async def send(self, address, text):
            sent.append(text); return Result()

    results = asyncio.run(notices.deliver_pending(sessions, Companion(), {"fake": Channel()}))
    assert sorted(r["status"] for r in results) == ["cancelled", "delivered"] and len(sent) == 1 and "http://h/x" in sent[0]
    assert asyncio.run(notices.deliver_pending(sessions, Companion(), {"fake": Channel()})) == []
    assert notices.delete_user("ana") == {"browser_notices": 2}


def test_legacy_sqlite_session_file_gains_host_column(tmp_path):
    path = tmp_path / "s.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE browser_sessions (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL, profile TEXT,
                  allowed_domains TEXT NOT NULL, url TEXT, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  closed_at TEXT, close_reason TEXT)""")
    db.execute("INSERT INTO browser_sessions VALUES('old','o','agent',NULL,'[]',NULL,NULL,'x','x',NULL,NULL)")
    db.commit(); db.close()
    store = SQLiteSessions(path, "host-a")
    assert store.mark_open_sessions_lost() == 1 and store.get_session("old")["state"] == "lost"

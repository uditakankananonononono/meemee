"""Browser session records and takeover notices across hosts (PostgreSQL mode).

With per-host browser-sessions.sqlite3 a session opened on API host A did not exist on host B
(404), account deletion on B left A's session records behind, and B could not tell a caller where
the session lived. The live browser stays on A; the records and the notice queue are shared.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN, _headers

TK = "bt_" + "a" * 32
TOKEN = "t" * 32
pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h


def test_session_held_by_a_is_listed_on_b_and_b_names_the_owning_host(hosts):
    from meemee.browser_sessions import default_host_id
    from meemee_persist_pg import BrowserSessionStore, Database, TakeoverNoticeQueue

    a, b = hosts["bases"]
    host_a = default_host_id(hosts["dirs"][0])
    db = Database(hosts["dsn"], min_size=1, max_size=2)
    try:
        store = BrowserSessionStore(db, host_a)  # what host A's manager writes when it opens a session
        store.create_session("bs_1", "admin", None, ["example.com"])
        store.event("bs_1", "agent", "opened", {"url": "https://example.com"})
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        store.create_takeover(TK, "bs_1", TOKEN, "captcha", "admin", expires)
        TakeoverNoticeQueue(db).enqueue({"takeover_id": TK, "reason": "captcha", "expires_at": expires.isoformat(),
                                         "url": f"{a}/browser/takeover?id={TK}"}, "bs_1", "ana")
    finally:
        db.close()
    admin = _headers()
    listed = httpx.get(f"{b}/v1/browser/sessions", headers=admin, timeout=10).json()["sessions"]
    assert [s["id"] for s in listed] == ["bs_1"] and listed[0]["host_id"] == host_a
    detail = httpx.get(f"{b}/v1/browser/sessions/bs_1", headers=admin, timeout=10)
    assert detail.status_code == 200 and [t["id"] for t in detail.json()["takeovers"]] == [TK]
    closed = httpx.delete(f"{b}/v1/browser/sessions/bs_1", headers=admin, timeout=10)
    assert closed.status_code == 409 and f"live on host {host_a}" in closed.json()["detail"], closed.text
    released = httpx.post(f"{b}/v1/browser/takeover/release", timeout=10,
                          json={"takeover_id": TK, "token": TOKEN, "outcome": "completed"})
    assert released.status_code == 403 and f"live on host {host_a}" in released.json()["detail"], released.text
    notices = httpx.get(f"{a}/v1/browser/notices", headers=admin, timeout=10).json()["notices"]
    assert [n["takeover_id"] for n in notices] == [TK]
    # host B's startup did not mark host A's session lost
    assert httpx.get(f"{a}/v1/browser/sessions/bs_1", headers=admin, timeout=10).json()["session"]["state"] == "agent"

    purged = httpx.delete(f"{b}/v1/admin/principals/admin/data", headers=admin, timeout=20)
    assert purged.status_code == 200, purged.text
    assert purged.json()["deleted"].get("browser_sessions") == 1
    assert httpx.get(f"{a}/v1/browser/sessions", headers=admin, timeout=10).json()["sessions"] == []

"""Monitors across hosts (PostgreSQL mode).

With per-host monitors.sqlite3 a monitor created through API host A was missing from host B's list,
its events were a 404 on B, and it could not be cancelled from B. Two API servers with separate
data directories share only PostgreSQL.
"""
from __future__ import annotations

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN, _headers

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h


def test_monitor_made_on_a_is_listed_and_cancelled_on_b(hosts):
    a, b = hosts["bases"]
    admin = _headers()
    made = httpx.post(f"{a}/v1/monitors", headers=admin, timeout=10,
                      json={"name": "price", "source_id": "shop", "field": "price", "operator": "lt", "expected": 100})
    assert made.status_code == 201, made.text
    ident = made.json()["id"]
    assert [m["id"] for m in httpx.get(f"{b}/v1/monitors", headers=admin, timeout=10).json()["monitors"]] == [ident]
    events = httpx.get(f"{b}/v1/monitors/{ident}/events", headers=admin, timeout=10)
    assert events.status_code == 200 and [e["kind"] for e in events.json()["events"]] == ["created"]
    assert httpx.delete(f"{b}/v1/monitors/{ident}", headers=admin, timeout=10).status_code == 200
    assert [m["status"] for m in httpx.get(f"{a}/v1/monitors", headers=admin, timeout=10).json()["monitors"]] == ["cancelled"]
    assert httpx.delete(f"{a}/v1/monitors/{ident}", headers=admin, timeout=10).status_code == 404
    kinds = [e["kind"] for e in httpx.get(f"{a}/v1/monitors/{ident}/events", headers=admin, timeout=10).json()["events"]]
    assert kinds == ["created", "cancelled"]


def test_both_hosts_report_the_shared_monitor_and_reflection_stores(hosts):
    for base in hosts["bases"]:
        ready = httpx.get(f"{base}/ready", timeout=10).json()["components"]
        assert ready["monitors"] == {"ok": True} and ready["reflection_schedule"] == {"ok": True}, ready


def test_two_reflection_workers_share_watermarks(hosts):
    """A second worker on another host does not re-reflect an owner the first one already covered."""
    import asyncio
    from datetime import timedelta

    from meemee.context import ContextRecord
    from meemee.reflection_schedule import reflect_due_once
    from meemee_persist_pg import ContextStore, Database, PersonalModelStore, ReflectionSchedule

    class Model:
        calls = 0

        async def chat(self, messages, temperature=0.1, max_tokens=None):
            Model.calls += 1
            return '{"claims": []}'

    db1, db2 = Database(hosts["dsn"], min_size=1, max_size=2), Database(hosts["dsn"], min_size=1, max_size=2)
    try:
        context = ContextStore(db1)
        context.register_source("ana", "mail", "gmail", {})
        context.ingest(ContextRecord(owner_id="ana", source_id="mail", external_id="1", kind="event", title="t",
                                     content="c", occurred_at="2026-01-01", provenance={}))
        first = asyncio.run(reflect_due_once(context, PersonalModelStore(db1), Model(), ReflectionSchedule(db1), timedelta(hours=1)))
        second = asyncio.run(reflect_due_once(ContextStore(db2), PersonalModelStore(db2), Model(), ReflectionSchedule(db2), timedelta(hours=1)))
        assert [s["owner_id"] for s in first] == ["ana"] and first[0]["status"] == "ok", first
        assert second == [] and Model.calls == 1
    finally:
        db1.close(); db2.close()

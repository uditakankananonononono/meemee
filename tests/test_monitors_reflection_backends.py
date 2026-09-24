"""Monitors and the reflection schedule: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.monitors import MonitorInput
from meemee.monitors import MonitorStore as SQLiteMonitors
from meemee.reflection_schedule import ReflectionSchedule as SQLiteSchedule
from meemee_persist_pg.interfaces import MonitorStoreInterface, ReflectionScheduleInterface


@pytest.fixture(params=BACKENDS)
def stores(request, tmp_path):
    if request.param == "sqlite":
        yield lambda: (SQLiteMonitors(tmp_path / "m.sqlite3"), SQLiteSchedule(tmp_path / "r.sqlite3"))
        return
    from meemee_persist_pg import Database, MigrationStore, MonitorStore, ReflectionSchedule

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield lambda: (MonitorStore(db), ReflectionSchedule(db))
    finally:
        db.close(); drop()


def _mon(name="price", **kw):
    base = {"name": name, "source_id": "shop", "field": "price", "operator": "lt", "expected": 100}
    return MonitorInput(**{**base, **kw})


def test_interfaces(stores):
    monitors, schedule = stores()
    assert isinstance(monitors, MonitorStoreInterface) and isinstance(schedule, ReflectionScheduleInterface)
    assert monitors.ping() and schedule.ping()


def test_monitor_lifecycle(stores):
    monitors, _ = stores()
    m = monitors.create("o", _mon(max_fires=2))
    assert m["predicate"] == {"expected": 100, "field": "price", "operator": "lt"} and m["status"] == "active"
    assert monitors.evaluate("o", "shop", {"price": 150}) == []
    assert monitors.evaluate("o", "shop", {"price": 90}) == [m["id"]]
    assert monitors.get("o", m["id"])["fire_count"] == 1
    assert monitors.evaluate("o", "shop", {"price": 80}) == [m["id"]]
    assert monitors.get("o", m["id"])["status"] == "completed"
    late = monitors.create("o", _mon("late", deadline="2020-01-01T00:00:00+00:00"))
    assert monitors.evaluate("o", "shop", {"price": 1}) == [] and monitors.get("o", late["id"])["status"] == "timed_out"
    c = monitors.create("o", _mon("word", field="title", operator="contains", expected="SALE"))
    assert monitors.cancel("o", c["id"]) and not monitors.cancel("o", c["id"])
    assert [e["kind"] for e in monitors.events("o", m["id"])] == ["created", "triggered", "triggered"]
    assert monitors.events("o", m["id"])[1]["payload"] == {"price": 90}
    assert [x["name"] for x in monitors.list("o", "cancelled")] == ["word"] and len(monitors.list("o")) == 3
    assert monitors.get("other", m["id"]) is None and not monitors.cancel("other", late["id"])
    assert monitors.delete_owner("o") == {"monitors": 3, "monitor_events": 7}
    assert monitors.list("o") == []


def test_concurrent_evaluation_never_exceeds_max_fires(stores):
    monitors, _ = stores()
    m = monitors.create("o", _mon(max_fires=3))
    with ThreadPoolExecutor(8) as pool:
        fired = sum(len(f) for f in pool.map(lambda _: monitors.evaluate("o", "shop", {"price": 1}), range(20)))
    assert fired == 3 and monitors.get("o", m["id"])["fire_count"] == 3


def test_reflection_schedule(stores):
    _, schedule = stores()
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    hour = timedelta(hours=1)
    assert schedule.due({"a": 5, "b": 2}, hour, now) == ["a", "b"]
    schedule.record("a", "ok", {"accepted": 1}, 5, now)
    schedule.record("b", "failed", {"error": "x"}, None, now)
    st = schedule.state("a")
    assert st["watermark"] == 5 and st["last_success_at"] == now.isoformat() and st["last_status"] == "ok"
    assert schedule.state("b")["watermark"] == 0 and schedule.state("b")["last_success_at"] is None
    assert schedule.due({"a": 5, "b": 2}, hour, now + timedelta(minutes=5)) == []
    assert schedule.due({"a": 6, "b": 2}, hour, now + 2 * hour) == ["a", "b"]
    schedule.record("a", "failed", {"error": "y"}, None, now + 2 * hour)
    st = schedule.state("a")
    assert st["watermark"] == 5 and st["last_success_at"] == now.isoformat() and st["last_status"] == "failed"
    assert schedule.delete_owner("a") == 1 and schedule.state("a") is None

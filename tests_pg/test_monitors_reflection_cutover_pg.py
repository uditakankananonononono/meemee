"""Monitors and reflection watermarks move to PostgreSQL with the cutover CLI."""
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

    name = "meemee_mr_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_monitors_and_reflection_state_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.monitors import MonitorInput, MonitorStore
    from meemee.plan_store import PlanStore
    from meemee.reflection_schedule import ReflectionSchedule
    from meemee_persist_pg import Database
    from meemee_persist_pg import MonitorStore as PGMonitors
    from meemee_persist_pg import ReflectionSchedule as PGSchedule
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    monitors = MonitorStore(tmp_path / "monitors.sqlite3")
    m = monitors.create("ana", MonitorInput(name="price", source_id="shop", field="price", operator="lt", expected=100, max_fires=2))
    monitors.evaluate("ana", "shop", {"price": 50})
    schedule = ReflectionSchedule(tmp_path / "reflection-schedule.sqlite3")
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    schedule.record("ana", "ok", {"accepted": 2}, 7, now)

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--monitors", str(tmp_path / "monitors.sqlite3"),
            "--reflection", str(tmp_path / "reflection-schedule.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_monitors"] == 1 and report["copied"]["meemee_monitor_events"] == 2
    assert report["copied"]["meemee_reflection_runs"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg, pgs = PGMonitors(db), PGSchedule(db)
        assert pg.list("ana") == monitors.list("ana") and pg.events("ana", m["id"]) == monitors.events("ana", m["id"])
        assert pgs.state("ana") == schedule.state("ana")
        assert pgs.due({"ana": 7}, timedelta(hours=1), now + timedelta(hours=2)) == []  # watermark carried over
        assert pg.evaluate("ana", "shop", {"price": 10}) == [m["id"]]  # second fire completes it after cutover
        assert pg.get("ana", m["id"])["status"] == "completed"
        assert [e["sequence"] for e in pg.events("ana", m["id"])] == [1, 2, 3]  # identity continues past copied rows
    finally:
        db.close()

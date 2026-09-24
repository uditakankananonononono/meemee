"""Companion profiles, facts, conversations, traces and check-ins move to PostgreSQL with the cutover CLI."""
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

    name = "meemee_comp_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_companion_state_survives_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.companion.models import CheckInPreferences, FactInput, PersonaConfig, UserProfile
    from meemee.companion.store import CompanionStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import CompanionStore as PGStore
    from meemee_persist_pg import Database
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    local = CompanionStore(tmp_path / "companion.sqlite3")
    local.upsert_user(UserProfile(user_id="ana", display_name="Ana", timezone="Asia/Kolkata",
                                  persona=PersonaConfig(), checkins=CheckInPreferences()))
    tea = local.add_fact("ana", FactInput(category="food", text="likes green tea", confidence=0.9), "chat")
    newer = local.add_fact("ana", FactInput(category="food", text="prefers oolong tea now", confidence=0.8), "chat")
    local.supersede_fact(tea["id"], newer["id"])
    local.start_conversation("ana", "local", "c1")
    local.add_message("c1", "user", "héllo"); reply = local.add_message("c1", "assistant", "hi Ana")
    local.record_model_trace(reply["id"], "c1", {"role": "chat", "profile": "local", "model": "m", "attempts": [{"ok": True}]})
    now = datetime.now(timezone.utc)
    local.schedule_checkin("ana", now - timedelta(minutes=5), "slot-due", "local", None)
    local.schedule_checkin("ana", now + timedelta(hours=2), "slot-later", "local", None)

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--companion", str(tmp_path / "companion.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_companion_facts"] == 2 and report["copied"]["meemee_companion_messages"] == 2
    assert report["copied"]["meemee_companion_checkins"] == 2 and report["copied"]["meemee_companion_message_models"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg = PGStore(db)
        assert pg.export_user_data("ana") == local.export_user_data("ana")
        assert [f["id"] for f in pg.search_facts("ana", "tea")] == [newer["id"]]
        assert pg.model_traces("c1") == local.model_traces("c1")
        added = pg.add_fact("ana", FactInput(category="x", text="new after cutover", confidence=0.5), "chat")
        assert added["id"] > newer["id"]  # identity continues after the copied ids
        assert pg.add_message("c1", "user", "again")["id"] > reply["id"]
        assert pg.claim_checkin(now)["slot"] == "slot-due" and pg.claim_checkin(now) is None
    finally:
        db.close()

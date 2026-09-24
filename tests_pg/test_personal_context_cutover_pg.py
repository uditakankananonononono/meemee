"""Personal model and connected context move to PostgreSQL with the cutover CLI."""
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

    name = "meemee_pc_cut_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(DSN, dbname=name)
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_personal_model_and_context_survive_cutover(dsn, tmp_path, capsys):
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    from meemee.context import ContextRecord, ContextStore
    from meemee.jobs import JobStore
    from meemee.memory import MemoryStore
    from meemee.personal_model import PersonalItemInput, PersonalModelStore
    from meemee.plan_store import PlanStore
    from meemee_persist_pg import ContextStore as PGContext
    from meemee_persist_pg import Database
    from meemee_persist_pg import PersonalModelStore as PGPersonal
    from meemee_persist_pg.cli import main

    MemoryStore(tmp_path / "meemee.sqlite3"); PlanStore(tmp_path / "plans.sqlite3"); JobStore(tmp_path / "jobs.sqlite3")
    AuditLog(tmp_path / "audit.sqlite3"); TokenStore(tmp_path / "auth.sqlite3")
    personal = PersonalModelStore(tmp_path / "personal-model.sqlite3")
    first = personal.upsert("ana", PersonalItemInput(kind="preference", title="city", value="Pune", confidence=0.7,
                                                     source_id="chat", source_record_id="m1"))
    current = personal.correct("ana", first["id"], "Delhi")
    context = ContextStore(tmp_path / "context.sqlite3")
    context.register_source("ana", "mail", "gmail", {"label": "inbox"})
    for ext, text in (("1", "dentist moved to Friday"), ("2", "lunch with Ravi")):
        context.ingest(ContextRecord(owner_id="ana", source_id="mail", external_id=ext, kind="event", title="Mail",
                                     content=text, occurred_at=f"2026-01-0{ext}", provenance={"id": ext}, cursor=f"c{ext}"))

    args = ["--dsn", dsn, "--memory", str(tmp_path / "meemee.sqlite3"), "--plans", str(tmp_path / "plans.sqlite3"),
            "--jobs", str(tmp_path / "jobs.sqlite3"), "--tokens", str(tmp_path / "auth.sqlite3"),
            "--audit", str(tmp_path / "audit.sqlite3"), "--personal", str(tmp_path / "personal-model.sqlite3"),
            "--context", str(tmp_path / "context.sqlite3")]
    assert main(args + ["copy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["copied"]["meemee_personal_items"] == 2 and report["copied"]["meemee_personal_evidence"] == 2
    assert report["copied"]["meemee_context_records"] == 2 and report["copied"]["meemee_context_sources"] == 1
    assert all(item["match"] for item in report["verification"].values()), report["verification"]

    db = Database(dsn, min_size=1, max_size=2)
    try:
        pg, pgc = PGPersonal(db), PGContext(db)
        assert pg.list("ana", include_history=True) == personal.list("ana", include_history=True)
        assert pg.get("ana", current["id"]) == personal.get("ana", current["id"])
        assert pgc.recent("ana") == context.recent("ana") and pgc.cursor("ana", "mail") == "c2"
        assert [r["external_id"] for r in pgc.search("ana", "dentist")] == ["1"]  # FTS index built from copied rows
        assert pgc.owner_watermarks() == context.owner_watermarks()
        # identity sequences continue past copied ids
        pgc.ingest(ContextRecord(owner_id="ana", source_id="mail", external_id="3", kind="event", title="Mail",
                                 content="new after cutover", occurred_at="2026-01-03", provenance={}))
        assert pgc.owner_watermarks()["ana"] == 3
        again = pg.correct("ana", current["id"], "Goa")
        assert again["evidence"][0]["source_id"] == "user"
    finally:
        db.close()
    assert main(args + ["verify"]) == 2  # PostgreSQL moved on after cutover; verify reports the drift

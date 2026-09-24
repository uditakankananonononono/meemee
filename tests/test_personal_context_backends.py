"""Personal model and connected context: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.context import ContextRecord
from meemee.context import ContextStore as SQLiteContext
from meemee.personal_model import PersonalItemInput
from meemee.personal_model import PersonalModelStore as SQLitePersonal
from meemee_persist_pg.interfaces import ContextStoreInterface, PersonalModelStoreInterface


@pytest.fixture(params=BACKENDS)
def stores(request, tmp_path):
    if request.param == "sqlite":
        yield lambda: (SQLitePersonal(tmp_path / "p.sqlite3"), SQLiteContext(tmp_path / "c.sqlite3"))
        return
    from meemee_persist_pg import ContextStore, Database, MigrationStore, PersonalModelStore

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield lambda: (PersonalModelStore(db), ContextStore(db))
    finally:
        db.close(); drop()


def _item(value, title="city", kind="preference", source="chat", record=None, confidence=0.8, **kw):
    return PersonalItemInput(kind=kind, title=title, value=value, confidence=confidence, source_id=source,
                             source_record_id=record or value, **kw)


def test_interfaces(stores):
    personal, context = stores()
    assert isinstance(personal, PersonalModelStoreInterface) and isinstance(context, ContextStoreInterface)
    assert personal.ping() and context.ping()


def test_upsert_merges_supersedes_and_keeps_evidence(stores):
    personal, _ = stores()
    first = personal.upsert("o", _item("Pune", confidence=0.6))
    same = personal.upsert("o", _item("Pune", record="r2", confidence=0.9))
    assert same["id"] == first["id"] and same["confidence"] == 0.9 and len(same["evidence"]) == 2
    moved = personal.upsert("o", _item("Delhi"))
    assert moved["supersedes_id"] == first["id"]
    assert personal.get("o", first["id"])["status"] == "superseded"
    assert [r["value"] for r in personal.list("o")] == ["Delhi"]
    # repeated corrections of one claim (older SQLite schema raised IntegrityError on the third)
    corrected = personal.correct("o", moved["id"], "Goa")
    again = personal.correct("o", corrected["id"], "Mumbai")
    assert again["value"] == "Mumbai" and again["evidence"][0]["source_id"] == "user"
    assert len(personal.list("o", include_history=True)) == 4
    assert personal.get("other", again["id"]) is None and personal.list("other") == []


def test_delete_decay_expire_context_and_purge(stores):
    personal, _ = stores()
    a = personal.upsert("o", _item("tea", title="drink"))
    b = personal.upsert("o", _item("run", title="habit", kind="routine"))
    personal.correct("o", b["id"], "swim")
    assert personal.decay("o", "9999", 0.5) == 1  # the user-corrected claim does not decay
    assert personal.get("o", a["id"])["confidence"] == pytest.approx(0.4)
    assert personal.list("o", kind="routine")[0]["value"] == "swim"
    trip = personal.upsert("o", _item("Paris", title="trip", kind="project", valid_until="2020-01-01T00:00:00+00:00"))
    assert personal.expire("o") == 1 and personal.get("o", trip["id"])["status"] == "superseded"
    assert personal.delete("o", a["id"]) and not personal.delete("o", a["id"])
    assert '"swim"' in personal.context("o") and "tea" not in personal.context("o")
    counts = personal.purge_owner("o")
    assert counts["personal_items"] == 4 and counts["personal_evidence"] >= 4
    assert personal.list("o", include_history=True) == []


def test_concurrent_same_claim_keeps_one_active(stores):
    personal, _ = stores()
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: personal.upsert("o", _item(f"v{i}", record=f"r{i}")), range(16)))
    assert len(personal.list("o")) == 1 and len(personal.list("o", include_history=True)) == 16


def _rec(ext, title, content, when, visibility="private", cursor=None, source="mail"):
    return ContextRecord(owner_id="o", source_id=source, external_id=ext, kind="event", title=title, content=content,
                         occurred_at=when, provenance={"url": f"x://{ext}"}, visibility=visibility, cursor=cursor,
                         metadata={"n": ext})


def test_context_sources_ingest_search_recent_assemble_purge(stores):
    _, context = stores()
    with pytest.raises(ValueError):
        context.ingest(_rec("1", "t", "c", "2026-01-01"))
    src = context.register_source("o", "mail", "gmail", {"b": 2, "a": 1})
    assert src["config"] == {"a": 1, "b": 2} and src["cursor"] is None
    assert context.ingest(_rec("1", "Dentist", "appointment moved to Friday", "2026-01-02", cursor="c1"))
    assert not context.ingest(_rec("1", "Dentist", "appointment moved to Friday", "2026-01-02"))  # same content dedupes
    assert context.ingest(_rec("1", "Dentist", "appointment moved to Monday", "2026-01-03"))  # new content is a new record
    assert context.ingest(_rec("2", "Lunch", "with Ravi", "2026-01-04", visibility="shared"))
    assert context.cursor("o", "mail") == "c1"
    hits = context.search("o", "appointment Friday")
    assert [h["content"] for h in hits] == ["appointment moved to Friday"]
    assert hits[0]["provenance"] == {"url": "x://1"} and hits[0]["metadata"] == {"n": "1"}
    assert context.search("o", "ravi") == [] and len(context.search("o", "ravi", allowed={"shared"})) == 1
    assert [r["occurred_at"] for r in context.recent("o")] == ["2026-01-03", "2026-01-02"]
    bundle = context.assemble("o", "Monday", 3)
    assert bundle["records"][0]["content"] == "appointment moved to Monday" and len(bundle["records"]) == 2
    assert bundle["sources"] == ["mail"]
    marks = context.owner_watermarks()
    assert set(marks) == {"o"} and marks["o"] == max(r["id"] for r in context.recent("o", allowed={"private", "shared"}))
    assert context.search("other", "appointment") == [] and context.source("other", "mail") is None
    assert context.purge_owner("o") == {"context_records": 3, "context_sources": 1}
    assert context.recent("o") == [] and context.search("o", "appointment") == []


def test_sqlite_legacy_personal_file_is_upgraded(tmp_path):
    path = tmp_path / "p.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE personal_items(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
      title TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')), valid_from TEXT, valid_until TEXT,
      supersedes_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner_id,kind,title,status));
      INSERT INTO personal_items VALUES('a','o','goal','t','1',1,'superseded',NULL,NULL,NULL,'x','x');
      INSERT INTO personal_items VALUES('b','o','goal','t','2',1,'active',NULL,NULL,'a','x','x');""")
    db.commit(); db.close()
    store = SQLitePersonal(path)
    store.upsert("o", _item("3", title="t", kind="goal"))
    store.upsert("o", _item("4", title="t", kind="goal"))
    assert [r["value"] for r in store.list("o")] == ["4"] and len(store.list("o", include_history=True)) == 4

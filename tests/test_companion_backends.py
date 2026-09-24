"""Companion state: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.companion.models import CheckInPreferences, FactInput, PersonaConfig, UserProfile
from meemee.companion.store import CompanionStore
from meemee_persist_pg.interfaces import CompanionStoreInterface


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path):
    if request.param == "sqlite":
        yield lambda: CompanionStore(tmp_path / "companion.sqlite3")
        return
    from meemee_persist_pg import CompanionStore as PGStore
    from meemee_persist_pg import Database, MigrationStore

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield lambda: PGStore(db)
    finally:
        db.close(); drop()


@pytest.fixture
def store(backend):
    return backend()


def _profile(user="u1", name="Ana"):
    return UserProfile(user_id=user, display_name=name, timezone="Asia/Kolkata",
                       persona=PersonaConfig(), checkins=CheckInPreferences())


def test_profiles_round_trip_and_upsert(store):
    assert isinstance(store, CompanionStoreInterface) and store.ping()
    created = store.upsert_user(_profile())
    assert created["display_name"] == "Ana" and isinstance(created["persona"], dict)
    updated = store.upsert_user(_profile(name="Ana B"))
    assert updated["display_name"] == "Ana B" and updated["created_at"] == created["created_at"]
    assert store.profile("u1") == _profile(name="Ana B") and store.profile("nobody") is None
    store.upsert_user(_profile("u2", "Bo"))
    assert [u["user_id"] for u in store.list_users()] == ["u2", "u1"]


def test_facts_supersede_find_and_search(store):
    tea = store.add_fact("u1", FactInput(category="food", text="likes green tea", confidence=0.9), "chat")
    coffee = store.add_fact("u1", FactInput(category="food", text="drinks black coffee daily", confidence=0.6), "chat")
    store.add_fact("u2", FactInput(category="food", text="likes green tea too", confidence=0.9), "chat")
    assert isinstance(tea["id"], int) and tea["superseded_by"] is None and tea["confidence"] == 0.9
    assert [f["id"] for f in store.list_facts("u1")] == [coffee["id"], tea["id"]]
    assert store.find_fact_text("u1", "likes green tea")["id"] == tea["id"] and store.find_fact_text("u1", "nope") is None
    hits = store.search_facts("u1", "tea coffee")
    assert {h["id"] for h in hits} == {tea["id"], coffee["id"]} and all("score" in h for h in hits)
    assert [h["id"] for h in store.search_facts("u1", "green")] == [tea["id"]]
    assert store.search_facts("u1", "") == [] and store.search_facts("u1", "unrelated") == []
    assert store.supersede_fact(tea["id"], coffee["id"]) and not store.supersede_fact(tea["id"])
    assert [f["id"] for f in store.list_facts("u1")] == [coffee["id"]]
    assert len(store.list_facts("u1", active_only=False)) == 2
    assert store.search_facts("u1", "green") == []
    assert store.supersede_fact(coffee["id"]) and store.get_fact(coffee["id"])["superseded_by"] == coffee["id"]
    assert store.get_fact(999999) is None


def test_conversations_messages_and_traces(store):
    first = store.start_conversation("u1", "local", "c1")
    assert store.start_conversation("u1", "local", "c1") == first
    store.start_conversation("u1", "sms")
    messages = [store.add_message("c1", role, f"m{i}") for i, role in enumerate(["user", "assistant", "user"])]
    assert [m["content"] for m in store.history("c1")] == ["m0", "m1", "m2"]
    assert [m["content"] for m in store.history("c1", limit=2)] == ["m1", "m2"]
    assert store.latest_conversation("u1", "local")["id"] == "c1" and store.latest_conversation("u1", "whatsapp") is None
    assert store.list_conversations("u1")[0]["id"] == "c1"  # most recent message first
    assert store.get_conversation("c1")["last_message_at"] == messages[-1]["created_at"]
    store.record_model_trace(messages[1]["id"], "c1", {"role": "chat", "profile": "local", "model": "m", "attempts": [{"ok": True}]})
    store.record_model_trace(messages[1]["id"], "c1", {"role": "chat", "profile": "inkling", "model": "m2", "attempts": []})
    [trace] = store.model_traces("c1")
    assert trace["profile"] == "inkling" and trace["attempts"] == []
    with pytest.raises(Exception):  # noqa: B017 - both backends enforce the role check
        store.add_message("c1", "robot", "x")


def test_checkins_schedule_claim_retry_finish_cancel(store):
    now = datetime.now(timezone.utc)
    row, created = store.schedule_checkin("u1", now - timedelta(minutes=1), "2026-09-24:morning", "local", None, max_attempts=2)
    again, created_again = store.schedule_checkin("u1", now, "2026-09-24:morning", "local", None)
    assert created and not created_again and again["id"] == row["id"]
    store.schedule_checkin("u1", now + timedelta(hours=1), "2026-09-24:evening", "local", None)
    claimed = store.claim_checkin(now)
    assert claimed["id"] == row["id"] and claimed["status"] == "queued" and store.claim_checkin(now) is None
    assert store.fail_checkin(row["id"], "boom") == "queued"
    store.claim_checkin(now)
    assert store.fail_checkin(row["id"], "boom") == "failed"
    [failed] = store.list_checkins("u1", status="failed")
    assert failed["attempts"] == 2 and failed["last_error"] == "boom"
    store.schedule_checkin("u1", now - timedelta(seconds=1), "2026-09-25:morning", "local", "addr")
    done = store.claim_checkin(now)
    store.finish_checkin(done["id"], "hello there")
    assert store.list_checkins("u1", status="done")[0]["message"] == "hello there"
    assert store.cancel_pending_checkins("u1") == 1
    assert [c["status"] for c in store.list_checkins("u1")] == ["cancelled", "done", "failed"]


def test_concurrent_checkin_claims_have_one_winner_each(backend):
    stores = [backend() for _ in range(4)]
    now = datetime.now(timezone.utc)
    for i in range(20):
        stores[0].schedule_checkin(f"u{i}", now - timedelta(seconds=1), "slot", "local", None)

    def drain(store):
        got = []
        while (c := store.claim_checkin(now)) is not None:
            got.append(c["id"])
        return got
    with ThreadPoolExecutor(8) as pool:
        claimed = [ident for batch in pool.map(drain, stores * 2) for ident in batch]
    assert len(claimed) == 20 and len(set(claimed)) == 20


def test_export_and_delete_are_user_scoped(store):
    store.upsert_user(_profile()); store.upsert_user(_profile("u2", "Bo"))
    store.add_fact("u1", FactInput(category="x", text="fact one", confidence=0.5), "chat")
    store.start_conversation("u1", "local", "c1"); msg = store.add_message("c1", "user", "hi")
    store.record_model_trace(msg["id"], "c1", {"attempts": []})
    store.schedule_checkin("u1", datetime.now(timezone.utc), "s", "local", None)
    store.start_conversation("u2", "local", "c2"); store.add_message("c2", "user", "other")
    exported = store.export_user_data("u1")
    assert exported["profile"]["user_id"] == "u1" and isinstance(exported["profile"]["persona"], str)
    assert [len(exported[k]) for k in ("facts", "conversations", "messages", "checkins", "model_traces")] == [1, 1, 1, 1, 1]
    assert exported["model_traces"][0]["attempts"] == "[]"
    assert store.delete_user_data("u1") == {"messages": 1, "model_traces": 1, "conversations": 1, "facts": 1, "checkins": 1, "profiles": 1}
    assert store.export_user_data("u1")["profile"] is None and store.history("c2")[0]["content"] == "other"

from datetime import datetime, timedelta, timezone
from pathlib import Path

from meemee.companion.models import FactInput, UserProfile
from meemee.companion.store import CompanionStore


def make_store(tmp_path: Path) -> CompanionStore:
    return CompanionStore(tmp_path / "companion.sqlite3")


def add_user(store: CompanionStore, user_id: str = "udita") -> dict:
    return store.upsert_user(UserProfile(user_id=user_id, display_name=user_id.title()))


def test_upsert_and_get_user(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    record = store.get_user("udita")
    assert record["display_name"] == "Udita"
    assert record["persona"]["display_name"] == "Meemee"
    updated = store.upsert_user(UserProfile(user_id="udita", display_name="Udita P", timezone="Asia/Calcutta"))
    assert updated["display_name"] == "Udita P" and updated["timezone"] == "Asia/Calcutta"
    assert store.get_user("nobody") is None


def test_profile_roundtrip_validates(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    profile = store.profile("udita")
    assert profile.user_id == "udita" and profile.persona.tone


def test_facts_add_search_supersede(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    fact = store.add_fact("udita", FactInput(category="project", text="Builds the Atlas agent platform"), "test")
    assert fact["id"] > 0 and fact["source"] == "test"
    hits = store.search_facts("udita", "Atlas platform")
    assert hits and hits[0]["text"].startswith("Builds")
    assert store.supersede_fact(fact["id"])
    assert store.list_facts("udita") == []
    assert len(store.list_facts("udita", active_only=False)) == 1
    assert store.search_facts("udita", "Atlas") == []


def test_fact_scrubs_secrets(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    secret = "sk-" + "a1" * 20
    fact = store.add_fact("udita", FactInput(text=f"her key is {secret} ok"), "test")
    assert secret not in fact["text"]
    assert "[REDACTED:" in fact["text"]


def test_find_fact_text_dedupe(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    store.add_fact("udita", FactInput(text="Drinks chai"), "test")
    assert store.find_fact_text("udita", "Drinks chai") is not None
    assert store.find_fact_text("udita", "Drinks coffee") is None


def test_conversations_and_history_order(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    conversation = store.start_conversation("udita", "local")
    store.add_message(conversation["id"], "user", "hello")
    store.add_message(conversation["id"], "assistant", "hi there")
    history = store.history(conversation["id"])
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert history[1]["content"] == "hi there"
    assert store.latest_conversation("udita", "local")["id"] == conversation["id"]
    assert store.latest_conversation("udita", "webhook") is None
    assert store.list_conversations("udita")[0]["channel"] == "local"


def test_history_limit_returns_most_recent_in_order(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    conversation = store.start_conversation("udita", "local")
    for index in range(10):
        store.add_message(conversation["id"], "user", f"m{index}")
    history = store.history(conversation["id"], limit=3)
    assert [row["content"] for row in history] == ["m7", "m8", "m9"]


def test_checkin_schedule_is_idempotent_per_slot(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    due = datetime.now(timezone.utc) + timedelta(hours=1)
    first, created1 = store.schedule_checkin("udita", due, "20260101T0000Z", "local", None)
    second, created2 = store.schedule_checkin("udita", due, "20260101T0000Z", "local", None)
    assert created1 and not created2
    assert first["id"] == second["id"]


def test_checkin_claim_finish_and_retry(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.schedule_checkin("udita", past, "slot-past", "local", None)
    claimed = store.claim_checkin()
    assert claimed["user_id"] == "udita"
    assert store.claim_checkin() is None
    assert store.fail_checkin(claimed["id"], "boom") == "queued"
    again = store.claim_checkin()
    assert again["id"] == claimed["id"]
    store.finish_checkin(again["id"], "message text")
    rows = store.list_checkins("udita", status="done")
    assert rows[0]["message"] == "message text"


def test_checkin_fail_exhausts_attempts(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    store.schedule_checkin("udita", past, "slot-x", "local", None, max_attempts=1)
    claimed = store.claim_checkin()
    assert store.fail_checkin(claimed["id"], "nope") == "failed"


def test_cancel_pending_checkins(tmp_path: Path):
    store = make_store(tmp_path)
    add_user(store)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    store.schedule_checkin("udita", future, "slot-c", "local", None)
    assert store.cancel_pending_checkins("udita") == 1
    assert store.list_checkins("udita", status="cancelled")
    assert store.cancel_pending_checkins("udita") == 0

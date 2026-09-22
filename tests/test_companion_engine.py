import json
from pathlib import Path

import pytest

from meemee.companion.engine import CompanionEngine
from meemee.companion.models import FactInput, PersonaConfig, UserProfile
from meemee.companion.store import CompanionStore


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls.append({"messages": messages, "temperature": temperature})
        return self.replies.pop(0)


def make_engine(tmp_path: Path, replies):
    store = CompanionStore(tmp_path / "c.db")
    model = FakeChat(replies)
    return CompanionEngine(model, store), store, model


@pytest.mark.asyncio
async def test_reply_persists_exchange_and_autoprovisions(tmp_path: Path):
    engine, store, model = make_engine(tmp_path, ["hey Udita", '{"facts": []}'])
    reply = await engine.reply("udita", "hello there")
    assert reply.reply == "hey Udita"
    assert reply.facts_learned == 0
    history = store.history(reply.conversation_id)
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert store.get_user("udita")["display_name"] == "udita"
    system = model.calls[0]["messages"][0]["content"]
    assert "none recorded yet" in system


@pytest.mark.asyncio
async def test_reply_grounds_prompt_in_persona_and_facts(tmp_path: Path):
    engine, store, model = make_engine(tmp_path, ["answer", '{"facts": []}'])
    store.upsert_user(UserProfile(
        user_id="udita", display_name="Udita",
        persona=PersonaConfig(display_name="Meemee", tone="blunt", style_rules=["no fluff"]),
    ))
    store.add_fact("udita", FactInput(category="project", text="Builds the Atlas agent platform"), "test")
    await engine.reply("udita", "what do you know about Atlas?")
    system = model.calls[0]["messages"][0]["content"]
    assert "blunt" in system and "no fluff" in system
    assert "Atlas agent platform" in system


@pytest.mark.asyncio
async def test_fact_extraction_stores_with_provenance_and_dedupes(tmp_path: Path):
    payload = json.dumps({"facts": [
        {"category": "preference", "text": "Prefers short answers"},
        {"category": "preference", "text": "Prefers short answers"},
        {"category": "junk"},
    ]})
    engine, store, _model = make_engine(tmp_path, ["noted", payload])
    reply = await engine.reply("udita", "keep answers short please")
    assert reply.facts_learned == 1
    facts = store.list_facts("udita")
    assert len(facts) == 1
    assert facts[0]["source"].startswith("conversation:")
    assert facts[0]["category"] == "preference"


@pytest.mark.asyncio
async def test_bad_extraction_payload_is_skipped(tmp_path: Path):
    engine, store, _model = make_engine(tmp_path, ["answer", "not json at all"])
    reply = await engine.reply("udita", "hi")
    assert reply.facts_learned == 0
    assert store.list_facts("udita") == []


@pytest.mark.asyncio
async def test_history_replayed_into_prompt(tmp_path: Path):
    engine, _store, model = make_engine(tmp_path, ["first reply", '{"facts": []}', "second reply", '{"facts": []}'])
    first = await engine.reply("udita", "message one")
    second = await engine.reply("udita", "message two", conversation_id=first.conversation_id)
    assert second.conversation_id == first.conversation_id
    contents = [m["content"] for m in model.calls[2]["messages"]]
    assert "message one" in contents and "first reply" in contents and "message two" in contents


@pytest.mark.asyncio
async def test_foreign_conversation_rejected(tmp_path: Path):
    engine, _store, _model = make_engine(tmp_path, ["r", '{"facts": []}'])
    mine = await engine.reply("udita", "hi")
    with pytest.raises(ValueError):
        await engine.reply("mallory", "snoop", conversation_id=mine.conversation_id)


@pytest.mark.asyncio
async def test_checkin_message_uses_persona_and_facts(tmp_path: Path):
    engine, store, model = make_engine(tmp_path, ["how is the essay going?"])
    store.upsert_user(UserProfile(user_id="udita", display_name="Udita", timezone="Asia/Calcutta"))
    store.add_fact("udita", FactInput(text="Writing a college essay about her grandmother"), "test")
    message = await engine.checkin_message("udita")
    assert message == "how is the essay going?"
    assert "college essay" in model.calls[0]["messages"][0]["content"]
    with pytest.raises(ValueError):
        await engine.checkin_message("ghost")

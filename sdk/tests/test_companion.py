"""Companion resource: request shapes, parsing and validation."""
from __future__ import annotations

import json

import httpx
import pytest
from conftest import NOW, make_client
from meemee_client import (
    CheckInPreferences,
    PersonaConfig,
    QuietHours,
)

USER = {
    "user_id": "udita",
    "display_name": "Udita",
    "timezone": "Asia/Calcutta",
    "persona": {"display_name": "Meemee", "tone": "blunt", "style_rules": ["no fluff"],
                "language": "en", "use_emoji": False, "custom_instructions": ""},
    "checkins": {"enabled": False, "cadence_minutes": 360, "quiet_hours": None,
                 "channel": "local", "address": None},
    "created_at": NOW,
    "updated_at": NOW,
}

FACT = {
    "id": 7, "user_id": "udita", "category": "project",
    "text": "Builds the Atlas agent platform", "confidence": 1.0,
    "source": "api:bootstrap", "superseded_by": None,
    "created_at": NOW, "updated_at": NOW,
}

CHECKIN = {
    "id": "ci1", "user_id": "udita", "slot": "20260922T1900Z",
    "due_at": NOW, "status": "queued", "attempts": 0, "max_attempts": 3,
    "channel": "local", "address": None, "message": None, "last_error": None,
    "created_at": NOW, "updated_at": NOW,
}


def test_upsert_and_get_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            body = json.loads(request.content.decode())
            assert body["display_name"] == "Udita"
            assert body["persona"]["tone"] == "blunt"
            assert body["checkins"]["quiet_hours"] == {"start": "22:00", "end": "06:00"}
            return httpx.Response(200, json=USER)
        assert request.url.path == "/v1/companion/users/udita"
        return httpx.Response(200, json=USER)

    client = make_client(handler)
    saved = client.companion.upsert_user(
        "udita", "Udita", timezone="Asia/Calcutta",
        persona=PersonaConfig(tone="blunt", style_rules=["no fluff"]),
        checkins=CheckInPreferences(quiet_hours=QuietHours(start="22:00", end="06:00")),
    )
    assert saved.persona.tone == "blunt"
    assert client.companion.get_user("udita").timezone == "Asia/Calcutta"


def test_list_users() -> None:
    client = make_client(lambda r: httpx.Response(200, json={"users": [USER]}))
    users = client.companion.list_users()
    assert users[0].user_id == "udita"


def test_update_persona_and_checkins() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/persona"):
            body = json.loads(request.content.decode())
            assert body["persona"]["use_emoji"] is True
            return httpx.Response(200, json=USER)
        assert request.url.path.endswith("/checkins")
        return httpx.Response(200, json={"user": USER, "cancelled_pending": 2})

    client = make_client(handler)
    assert client.companion.update_persona("udita", PersonaConfig(use_emoji=True)).user_id == "udita"
    result = client.companion.update_checkins("udita", CheckInPreferences(enabled=False))
    assert result.cancelled_pending == 2 and result.user.user_id == "udita"


def test_fact_crud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode())
            assert body == {"category": "project", "text": "Builds Atlas", "confidence": 1.0}
            return httpx.Response(201, json=FACT)
        if request.method == "DELETE":
            assert request.url.path == "/v1/companion/users/udita/facts/7"
            return httpx.Response(200, json={"fact_id": 7, "active": False})
        assert httpx.QueryParams(request.url.params)["query"] == "Atlas"
        return httpx.Response(200, json={"facts": [FACT]})

    client = make_client(handler)
    fact = client.companion.add_fact("udita", "Builds Atlas", category="project")
    assert fact.id == 7 and fact.source == "api:bootstrap"
    assert client.companion.list_facts("udita", query="Atlas")[0].text.startswith("Builds")
    retired = client.companion.retire_fact("udita", 7)
    assert retired.active is False


def test_chat_posts_turn_and_parses_reply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body == {"user_id": "udita", "text": "hello", "channel": "local",
                        "conversation_id": "conv-1"}
        return httpx.Response(200, json={
            "conversation_id": "conv-1", "reply": "hey", "facts_learned": 1, "persona": "Meemee"})

    client = make_client(handler)
    reply = client.companion.chat("udita", "hello", conversation_id="conv-1")
    assert reply.reply == "hey" and reply.facts_learned == 1


def test_chat_rejects_empty_text() -> None:
    client = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client.companion.chat("udita", "   ")


def test_conversations_and_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [
                {"id": 1, "conversation_id": "conv-1", "role": "user", "content": "hi", "created_at": NOW},
                {"id": 2, "conversation_id": "conv-1", "role": "assistant", "content": "hey", "created_at": NOW},
            ]})
        return httpx.Response(200, json={"conversations": [
            {"id": "conv-1", "user_id": "udita", "channel": "local",
             "created_at": NOW, "last_message_at": NOW}]})

    client = make_client(handler)
    assert client.companion.list_conversations("udita")[0].channel == "local"
    messages = client.companion.messages("conv-1")
    assert [m.role for m in messages] == ["user", "assistant"]


def test_checkin_plan_list_and_tick() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checkins/plan"):
            return httpx.Response(201, json=CHECKIN)
        if request.url.path.endswith("/checkins/tick"):
            return httpx.Response(200, json={"planned": 1, "deliveries": [
                {"claimed": True, "checkin_id": "ci1", "delivered": True}]})
        assert httpx.QueryParams(request.url.params)["status"] == "queued"
        return httpx.Response(200, json={"checkins": [CHECKIN]})

    client = make_client(handler)
    assert client.companion.plan_checkin("udita").slot == "20260922T1900Z"
    assert client.companion.list_checkins("udita", status="queued")[0].status == "queued"
    tick = client.companion.tick()
    assert tick.planned == 1 and tick.deliveries[0]["delivered"] is True

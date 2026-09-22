import os

os.environ["MEEMEE_API_TOKEN"] = "test-bootstrap-token"

import pytest
from fastapi.testclient import TestClient

from meemee.api import app, companion

client = TestClient(app)
headers = {"Authorization": "Bearer test-bootstrap-token"}


class FakeChat:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls.append(messages)
        if messages[0]["role"] == "user" and "Extract durable facts" in messages[0]["content"]:
            return '{"facts": [{"category": "preference", "text": "Prefers direct answers"}]}'
        return "companion reply"


@pytest.fixture(autouse=True)
def fake_model():
    original = companion.engine.model
    companion.engine.model = FakeChat()
    yield companion.engine.model
    companion.engine.model = original


def make_user(user_id="api-user"):
    response = client.put(f"/v1/companion/users/{user_id}", headers=headers, json={
        "display_name": "API User", "timezone": "Asia/Calcutta",
        "persona": {"tone": "blunt", "style_rules": ["no fluff"]},
    })
    assert response.status_code == 200
    return response


def test_companion_requires_auth():
    assert client.get("/v1/companion/users").status_code == 401


def test_companion_scope_enforced():
    created = client.post("/v1/tokens", headers=headers, json={"name": "jobs-only", "scopes": ["jobs:read"]})
    token = created.json()["token"]
    response = client.get("/v1/companion/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    reader = client.post("/v1/tokens", headers=headers, json={"name": "companion-reader", "scopes": ["companion:read"]})
    token = reader.json()["token"]
    assert client.get("/v1/companion/users", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    denied = client.put("/v1/companion/users/x", headers={"Authorization": f"Bearer {token}"},
                        json={"display_name": "X"})
    assert denied.status_code == 403


def test_upsert_get_and_persona_update():
    make_user()
    record = client.get("/v1/companion/users/api-user", headers=headers).json()
    assert record["persona"]["tone"] == "blunt"
    assert record["timezone"] == "Asia/Calcutta"
    updated = client.put("/v1/companion/users/api-user/persona", headers=headers, json={
        "persona": {"display_name": "Meemee", "tone": "gentle", "use_emoji": True}
    })
    assert updated.status_code == 200
    assert updated.json()["persona"]["tone"] == "gentle"
    assert updated.json()["persona"]["use_emoji"] is True


def test_upsert_rejects_bad_timezone():
    response = client.put("/v1/companion/users/bad-tz", headers=headers, json={
        "display_name": "X", "timezone": "Nowhere/Special"})
    assert response.status_code == 422


def test_unknown_user_404():
    assert client.get("/v1/companion/users/ghost", headers=headers).status_code == 404
    assert client.get("/v1/companion/users/ghost/facts", headers=headers).status_code == 404


def test_fact_crud_and_search():
    make_user("fact-user")
    created = client.post("/v1/companion/users/fact-user/facts", headers=headers, json={
        "category": "project", "text": "Builds the Atlas agent platform"})
    assert created.status_code == 201
    fact_id = created.json()["id"]
    listed = client.get("/v1/companion/users/fact-user/facts", headers=headers).json()["facts"]
    assert any(fact["id"] == fact_id for fact in listed)
    hits = client.get("/v1/companion/users/fact-user/facts?query=Atlas", headers=headers).json()["facts"]
    assert hits and hits[0]["id"] == fact_id
    retired = client.delete(f"/v1/companion/users/fact-user/facts/{fact_id}", headers=headers)
    assert retired.status_code == 200
    assert client.get("/v1/companion/users/fact-user/facts", headers=headers).json()["facts"] == []


def test_chat_roundtrip_and_history():
    make_user("chat-user")
    reply = client.post("/v1/companion/chat", headers=headers, json={
        "user_id": "chat-user", "text": "hello companion"})
    assert reply.status_code == 200
    body = reply.json()
    assert body["reply"] == "companion reply"
    assert body["facts_learned"] == 1
    conversations = client.get("/v1/companion/users/chat-user/conversations", headers=headers).json()["conversations"]
    assert conversations[0]["id"] == body["conversation_id"]
    messages = client.get(
        f"/v1/companion/conversations/{body['conversation_id']}/messages", headers=headers
    ).json()["messages"]
    assert [row["role"] for row in messages] == ["user", "assistant"]
    facts = client.get("/v1/companion/users/chat-user/facts", headers=headers).json()["facts"]
    assert facts[0]["text"] == "Prefers direct answers"


def test_checkin_preferences_and_planning():
    make_user("plan-user")
    updated = client.put("/v1/companion/users/plan-user/checkins", headers=headers, json={
        "checkins": {"enabled": True, "cadence_minutes": 30,
                     "quiet_hours": {"start": "22:00", "end": "06:00"}, "channel": "local"}
    })
    assert updated.status_code == 200
    planned = client.post("/v1/companion/users/plan-user/checkins/plan", headers=headers)
    assert planned.status_code == 201
    assert planned.json()["status"] == "queued"
    listed = client.get("/v1/companion/users/plan-user/checkins", headers=headers).json()["checkins"]
    assert listed[0]["id"] == planned.json()["id"]
    disabled = client.put("/v1/companion/users/plan-user/checkins", headers=headers, json={
        "checkins": {"enabled": False}})
    assert disabled.status_code == 200
    assert disabled.json()["cancelled_pending"] >= 1
    conflict = client.post("/v1/companion/users/plan-user/checkins/plan", headers=headers)
    assert conflict.status_code == 409


def test_checkin_tick_requires_admin_scope():
    created = client.post("/v1/tokens", headers=headers, json={"name": "cw", "scopes": ["companion:write"]})
    token = created.json()["token"]
    response = client.post("/v1/companion/checkins/tick", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    ok = client.post("/v1/companion/checkins/tick", headers=headers)
    assert ok.status_code == 200
    assert "planned" in ok.json() and "deliveries" in ok.json()

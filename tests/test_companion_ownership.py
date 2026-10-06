"""Cross-owner isolation canaries for the companion HTTP API.

A principal's companion scopes must only reach its own companion user; anything
else fails closed (404, like a nonexistent user; 403 for the admin-only user
inventory). Regression coverage for the cross-owner read/write IDOR found by
source audit and reproduced live with a separate owner's token.
"""
import os

os.environ["MEEMEE_API_TOKEN"] = "test-bootstrap-token"

from fastapi.testclient import TestClient

from meemee.api import app, companion, tokens

client = TestClient(app)
ADMIN = {"Authorization": "Bearer test-bootstrap-token"}


class FakeChat:
    async def chat(self, messages, temperature=0.7, max_tokens=None):
        if messages[0]["role"] == "user" and "Extract durable facts" in messages[0]["content"]:
            return '{"facts": []}'
        return "companion reply"


def attacker_headers(owner="attacker-owner"):
    _, raw = tokens.create(
        "attacker", {"companion:read", "companion:write"}, owner_id=owner, token_kind="api"
    )
    return {"Authorization": f"Bearer {raw}"}


def seed_victim():
    client.put("/v1/companion/users/victim-owner", headers=ADMIN, json={
        "display_name": "Victim", "timezone": "Asia/Calcutta",
    })
    response = client.post("/v1/companion/users/victim-owner/facts", headers=ADMIN,
                           json={"category": "general", "text": "PRIVATE CROSS OWNER CANARY"})
    assert response.status_code == 201
    return response.json()["id"]


def test_cross_owner_read_and_write_are_denied():
    fact_id = seed_victim()
    headers = attacker_headers()
    # Cross-owner reads: user, facts, conversations, check-ins.
    assert client.get("/v1/companion/users/victim-owner", headers=headers).status_code == 404
    assert client.get("/v1/companion/users/victim-owner/facts", headers=headers).status_code == 404
    assert client.get("/v1/companion/users/victim-owner/conversations", headers=headers).status_code == 404
    assert client.get("/v1/companion/users/victim-owner/checkins", headers=headers).status_code == 404
    # Cross-owner writes: persona, check-ins, fact add/retire, check-in plan.
    assert client.put("/v1/companion/users/victim-owner/persona", headers=headers,
                      json={"persona": {"display_name": "CHANGED"}}).status_code == 404
    assert client.put("/v1/companion/users/victim-owner/checkins", headers=headers,
                      json={"checkins": {"enabled": True}}).status_code == 404
    assert client.post("/v1/companion/users/victim-owner/facts", headers=headers,
                       json={"text": "injected"}).status_code == 404
    assert client.delete(f"/v1/companion/users/victim-owner/facts/{fact_id}", headers=headers).status_code == 404
    assert client.post("/v1/companion/users/victim-owner/checkins/plan", headers=headers).status_code == 404
    # Cross-owner profile creation/overwrite is also denied.
    assert client.put("/v1/companion/users/victim-owner", headers=headers,
                      json={"display_name": "Hijack"}).status_code == 404
    # The victim's data is untouched.
    fact = client.get("/v1/companion/users/victim-owner/facts", headers=ADMIN)
    assert fact.status_code == 200 and fact.json()["facts"][0]["text"] == "PRIVATE CROSS OWNER CANARY"


def test_cross_owner_conversation_messages_denied():
    original = companion.engine.model
    companion.engine.model = FakeChat()
    try:
        reply = client.post("/v1/companion/chat", headers=ADMIN,
                            json={"user_id": "victim-owner", "text": "hello"})
        assert reply.status_code == 200
        conversation_id = reply.json()["conversation_id"]
    finally:
        companion.engine.model = original
    headers = attacker_headers()
    assert client.get(f"/v1/companion/conversations/{conversation_id}/messages",
                      headers=headers).status_code == 404
    assert client.post("/v1/companion/chat", headers=headers,
                       json={"user_id": "victim-owner", "text": "impersonate"}).status_code == 404


def test_user_inventory_requires_admin():
    headers = attacker_headers()
    assert client.get("/v1/companion/users", headers=headers).status_code == 403
    assert client.get("/v1/companion/users", headers=ADMIN).status_code == 200


def test_owner_can_manage_own_companion_user():
    original = companion.engine.model
    companion.engine.model = FakeChat()
    try:
        headers = attacker_headers(owner="self-serve-customer")
        created = client.put("/v1/companion/users/self-serve-customer", headers=headers,
                             json={"display_name": "Self Serve", "timezone": "UTC"})
        assert created.status_code == 200
        reply = client.post("/v1/companion/chat", headers=headers,
                            json={"user_id": "self-serve-customer", "text": "hi"})
        assert reply.status_code == 200 and reply.json()["reply"] == "companion reply"
        assert client.get("/v1/companion/users/self-serve-customer/facts", headers=headers).status_code == 200
    finally:
        companion.engine.model = original

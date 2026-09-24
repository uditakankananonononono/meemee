"""Token introspection: store semantics and the admin HTTP endpoint."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from meemee import api
from meemee.audit import AuditLog
from meemee.auth import TokenStore, token_state

ADMIN = {"Authorization": "Bearer test-bootstrap-token"}


def _isolate(monkeypatch, tmp_path) -> TokenStore:
    store = TokenStore(tmp_path / "auth.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    monkeypatch.setattr(api, "audit", AuditLog(tmp_path / "audit.db"))
    return store


def test_store_introspect_states_and_no_side_effects(tmp_path) -> None:
    store = TokenStore(tmp_path / "auth.db")
    ident, secret = store.create("ci", {"jobs:read", "jobs:write"}, owner_id="acct-1")
    record = store.introspect(secret)
    assert record["active"] is True and record["state"] == "active"
    assert record["id"] == ident and record["principal"] == "acct-1"
    assert record["scopes"] == ["jobs:read", "jobs:write"]
    assert record["last_used_at"] is None
    assert secret not in repr(record) and "digest" not in record
    # Introspection is not use; authentication is.
    store.introspect(secret)
    assert store.introspect(secret)["last_used_at"] is None
    assert store.authenticate(secret) is not None
    assert store.introspect(secret)["last_used_at"] is not None

    assert store.revoke(ident)
    revoked = store.introspect(secret)
    assert revoked["active"] is False and revoked["state"] == "revoked" and revoked["revoked_at"]
    assert store.authenticate(secret) is None

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _, expired_secret = store.create("old", {"jobs:read"}, expires_at=past)
    assert store.introspect(expired_secret)["state"] == "expired"
    assert store.authenticate(expired_secret) is None
    assert store.introspect("mee_never_issued") is None


def test_token_state_rules() -> None:
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert token_state(None, None, now) == "active"
    assert token_state("2026-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", now) == "revoked"
    assert token_state(None, "2026-09-23T23:59:59Z", now) == "expired"
    assert token_state(None, "2026-09-24T00:00:01+00:00", now) == "active"
    assert token_state(None, "2026-09-25T00:00:00", now) == "active"  # naive means UTC
    assert token_state(None, "garbage", now) == "expired"


def test_endpoint_reports_each_state_redacted_and_audited(monkeypatch, tmp_path) -> None:
    store = _isolate(monkeypatch, tmp_path)
    client = TestClient(api.app)
    ident, secret = store.create("worker", {"jobs:read"}, expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())

    body = client.post("/v1/tokens/introspect", json={"token": secret}, headers=ADMIN).json()
    assert body["active"] is True and body["state"] == "active" and body["credential"] == "api_token"
    assert body["id"] == ident and body["scopes"] == ["jobs:read"] and body["expires_at"]
    assert body["redacted_token"] == f"{secret[:4]}...{secret[-4:]}"
    assert secret not in str(body)

    store.revoke(ident)
    assert client.post("/v1/tokens/introspect", json={"token": secret}, headers=ADMIN).json()["state"] == "revoked"

    unknown = client.post("/v1/tokens/introspect", json={"token": "mee_unknown_value_123"}, headers=ADMIN)
    assert unknown.status_code == 200
    assert unknown.json() == {"redacted_token": "mee_..._123", "active": False, "state": "unknown", "credential": None, "scopes": []}

    boot = client.post("/v1/tokens/introspect", json={"token": "test-bootstrap-token"}, headers=ADMIN).json()
    assert boot["credential"] == "bootstrap" and boot["active"] and "admin" in boot["scopes"]
    assert "test-bootstrap-token" not in str(boot)

    entries = api.audit.list(0, 100)
    introspections = [e for e in entries if e["action"] == "token.introspect"]
    assert [e["metadata"]["state"] for e in introspections] == ["active", "revoked", "unknown", "active"]
    assert all(secret not in str(e) for e in entries)


def test_endpoint_requires_admin_and_validates_body(monkeypatch, tmp_path) -> None:
    store = _isolate(monkeypatch, tmp_path)
    client = TestClient(api.app)
    _, reader = store.create("reader", {"jobs:read"})
    assert client.post("/v1/tokens/introspect", json={"token": reader}).status_code == 401
    denied = client.post("/v1/tokens/introspect", json={"token": reader}, headers={"Authorization": f"Bearer {reader}"})
    assert denied.status_code == 403 and denied.json()["detail"] == "missing scope: admin"
    assert client.post("/v1/tokens/introspect", json={"token": ""}, headers=ADMIN).status_code == 422
    assert client.post("/v1/tokens/introspect", json={}, headers=ADMIN).status_code == 422

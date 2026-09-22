from fastapi.testclient import TestClient

from meemee import api


def test_whoami_returns_exact_identity_scopes_and_entitlement(monkeypatch, tmp_path):
    from meemee.approvals import ApprovalStore
    from meemee.entitlements import EntitlementStore
    from meemee.quotas import QuotaStore
    from meemee.webhooks import WebhookStore

    monkeypatch.setattr(api,"entitlements",EntitlementStore(tmp_path/"e.db"))
    monkeypatch.setattr(api,"quotas",QuotaStore(tmp_path/"q.db"))
    monkeypatch.setattr(api,"approvals",ApprovalStore(tmp_path/"a.db"))
    monkeypatch.setattr(api,"webhooks",WebhookStore(tmp_path/"w.db",encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="))
    response=TestClient(api.app).get("/v1/whoami",headers={"Authorization":"Bearer test-bootstrap-token"})
    assert response.status_code==200
    body=response.json()
    assert body["id"]=="bootstrap" and body["name"]=="bootstrap"
    assert body["scopes"]==["admin","companion:read","companion:write","jobs:read","jobs:write","runs:write"]
    assert body["entitlement"]["plan"]=="starter"
    assert body["entitlement"]["usage"]["daily_jobs"]==0


def test_whoami_requires_authentication():
    assert TestClient(api.app).get("/v1/whoami").status_code==401

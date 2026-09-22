from fastapi.testclient import TestClient

from meemee import api


def test_api_and_console_security_headers():
    client = TestClient(api.app)
    api_response = client.get("/v1/quota", headers={"Authorization":"Bearer test-bootstrap-token"})
    assert api_response.status_code == 200
    assert api_response.headers["cache-control"] == "no-store"
    assert api_response.headers["pragma"] == "no-cache"
    assert api_response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in api_response.headers["content-security-policy"]
    assert api_response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    console = client.get("/console/")
    assert console.status_code == 200
    assert "default-src 'self'" in console.headers["content-security-policy"]
    assert "cache-control" not in console.headers


def test_untrusted_host_is_rejected():
    response = TestClient(api.app).get("/health", headers={"Host":"attacker.example"})
    assert response.status_code == 400


def test_hsts_is_operator_controlled(monkeypatch):
    monkeypatch.setattr(api.settings, "hsts_enabled", True)
    monkeypatch.setattr(api.settings, "hsts_max_age_seconds", 12345)
    response = TestClient(api.app).get("/health")
    assert response.headers["strict-transport-security"] == "max-age=12345; includeSubDomains"

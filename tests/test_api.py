import os

os.environ["MEEMEE_API_TOKEN"] = "test-bootstrap-token"

from fastapi.testclient import TestClient

from meemee.api import app

client = TestClient(app)
headers = {"Authorization": "Bearer test-bootstrap-token"}


def test_health_and_security_headers():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "0.118.0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


def test_run_requires_auth_before_validation():
    response = client.post("/v1/runs", json={"goal": "valid goal"})
    assert response.status_code == 401


def test_run_validation_with_auth():
    response = client.post("/v1/runs", headers=headers, json={"goal": "x"})
    assert response.status_code == 422


def test_create_and_revoke_scoped_token():
    created = client.post("/v1/tokens", headers=headers, json={"name": "reader", "scopes": ["jobs:read"]})
    assert created.status_code == 200
    token = created.json()["token"]
    forbidden = client.post("/v1/jobs", headers={"Authorization": f"Bearer {token}"}, json={"goal": "work"})
    assert forbidden.status_code == 403
    revoked = client.delete(f"/v1/tokens/{created.json()['id']}", headers=headers)
    assert revoked.status_code == 200


def test_console_is_mounted():
    response = client.get("/console/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Meemee" in response.text

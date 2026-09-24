import os

os.environ["MEEMEE_API_TOKEN"] = "test-bootstrap-token"

import httpx
import respx
from fastapi.testclient import TestClient

from meemee import api
from meemee.api import app

client = TestClient(app)
headers = {"Authorization": "Bearer test-bootstrap-token"}


def test_models_status_requires_auth():
    assert client.get("/v1/models/status").status_code == 401


def test_models_status_lists_profiles_without_secrets(monkeypatch):
    monkeypatch.setattr(api.settings, "hf_token", "hf_secret_value")
    monkeypatch.setattr(api.settings, "model_routes", "chat=inkling,local")
    r = client.get("/v1/models/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "hf_secret_value" not in r.text
    names = {p["name"]: p for p in body["profiles"]}
    assert names["inkling"]["key_configured"] and names["inkling"]["available"]
    assert names["fugu"]["available"] is False
    assert body["routes"]["chat"] == ["inkling", "local"]
    assert "probes" not in body


@respx.mock
def test_models_status_probe_reports_serving_profile(monkeypatch):
    monkeypatch.setattr(api.settings, "hf_token", "hf_x")
    monkeypatch.setattr(api.settings, "model_base_url", "http://local.test/v1")
    monkeypatch.setattr(api.settings, "model_routes", "chat=inkling,local;agent=local")
    respx.get("https://router.huggingface.co/v1/models").mock(return_value=httpx.Response(401))
    respx.get("http://local.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": api.settings.model_name}]}))
    body = client.get("/v1/models/status?probe=true", headers=headers).json()
    assert body["probes"]["inkling"]["reachable"] is False
    assert body["probes"]["local"]["model_listed"] is True
    assert body["serving"] == {"agent": "local", "chat": "local", "reflection": "local"}

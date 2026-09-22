import socket
from pathlib import Path

from fastapi.testclient import TestClient

from meemee import api
from meemee.webhooks import WebhookStore

TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

def public_dns(*args): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_webhook_api_create_list_delete(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    fresh = WebhookStore(tmp_path / "webhooks.db", encryption_key=TEST_KEY)
    monkeypatch.setattr(api, "webhooks", fresh)
    client = TestClient(api.app)
    headers = {"Authorization":"Bearer test-bootstrap-token"}
    created = client.post("/v1/webhooks", headers=headers, json={"url":"https://hooks.example/mee","events":["job.done"]})
    assert created.status_code == 200 and created.json()["secret"]
    listed = client.get("/v1/webhooks", headers=headers).json()["webhooks"]
    assert len(listed) == 1 and "secret" not in listed[0]
    deleted = client.delete(f"/v1/webhooks/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 200


def test_terminal_event_enqueue_is_idempotent(tmp_path: Path, monkeypatch):
    store = WebhookStore(tmp_path / "webhooks.db", encryption_key=TEST_KEY)
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store.subscribe("u", "https://hooks.example/mee", {"job.done"})
    payload = {"job_id":"j1","status":"done"}
    assert store.enqueue("job:j1:done", "job.done", payload) == 1
    assert store.enqueue("job:j1:done", "job.done", payload) == 0

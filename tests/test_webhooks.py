import socket
from pathlib import Path

import httpx
import pytest

from meemee.webhooks import WebhookDispatcher, WebhookStore, validate_webhook_url

TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

def public_dns(*args): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_subscription_outbox_is_deduplicated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db", encryption_key=TEST_KEY)
    ident, secret = store.subscribe("u", "https://hooks.example/mee", {"job.done"})
    assert ident and len(secret) > 20
    stored = store.db.execute("SELECT secret FROM webhook_subscriptions WHERE id=?", (ident,)).fetchone()[0]
    assert stored.startswith("enc:v1:") and secret not in stored
    assert store.enqueue("evt-1", "job.done", {"id":"j"}) == 1
    assert store.enqueue("evt-1", "job.done", {"id":"j"}) == 0
    assert store.enqueue("evt-2", "other", {}) == 0


def test_webhook_rejects_private_or_insecure(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"): validate_webhook_url("http://example.com/hook")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2,1,6,"",("127.0.0.1",443))])
    with pytest.raises(ValueError, match="private"): validate_webhook_url("https://example.com/hook")


@pytest.mark.asyncio
async def test_signed_delivery_succeeds(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db", encryption_key=TEST_KEY); store.subscribe("u", "https://hooks.example/mee", {"job.done"}); store.enqueue("e", "job.done", {"ok":True})
    seen = {}
    def handler(request):
        seen.update(request.headers); return httpx.Response(204, request=request)
    dispatcher = WebhookDispatcher(store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await dispatcher.deliver_one()
    assert seen["x-meemee-signature-256"].startswith("sha256=")
    assert store.db.execute("SELECT status FROM webhook_deliveries").fetchone()[0] == "delivered"


@pytest.mark.asyncio
async def test_failed_delivery_requeues(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db", encryption_key=TEST_KEY); store.subscribe("u", "https://hooks.example/mee", {"*"}); store.enqueue("e", "job.failed", {})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, request=r)))
    assert await WebhookDispatcher(store, client).deliver_one()
    row = store.db.execute("SELECT status,attempts,last_error FROM webhook_deliveries").fetchone()
    assert tuple(row) == ("queued",1,"HTTP 503")

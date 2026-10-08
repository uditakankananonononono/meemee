"""Area 216: delivery-time destination re-validation. DNS monkeypatched, MockTransport only.

Closes the re-validation gap only; it does NOT close the connect-time rebinding race.
Tests marked PROTECTION already pass on base.
"""
import socket
from pathlib import Path

import httpx
import pytest

from meemee.webhooks import WebhookDispatcher, WebhookStore

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _dns_to(addr):
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    return lambda *a, **k: [(fam, socket.SOCK_STREAM, 6, "", (addr, 443))]


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", _dns_to("93.184.216.34"))
    s = WebhookStore(tmp_path / "w.db", encryption_key=KEY)
    s.subscribe("u", "https://hooks.example/mee", {"*"})
    s.enqueue("e", "job.done", {}, principal="u")
    yield s
    s.db.close()


def _row(store):
    return tuple(store.db.execute("SELECT status,attempts,last_error FROM webhook_deliveries").fetchone())


async def _deliver(store, handler=None, seen=None):
    seen = [] if seen is None else seen

    def h(r):
        seen.append(str(r.url))
        return httpx.Response(204, request=r)
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler or h))
    try:
        return await WebhookDispatcher(store, c).deliver_one()
    finally:
        await c.aclose()


REBOUND = ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "fe80::1", "0.0.0.0"]


@pytest.mark.parametrize("addr", REBOUND)
async def test_host_rebound_to_private_after_subscribe_is_not_posted(store, monkeypatch, addr):
    monkeypatch.setattr(socket, "getaddrinfo", _dns_to(addr))
    seen = []
    assert await _deliver(store, seen=seen) is True
    assert seen == []
    assert _row(store) == ("queued", 1, "destination not allowed")


async def test_mixed_public_and_private_answers_are_refused(store, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, 1, 6, "", ("93.184.216.34", 443)), (socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))])
    seen = []
    await _deliver(store, seen=seen)
    assert seen == [] and _row(store)[2] == "destination not allowed"


async def test_dns_failure_is_a_contained_retry(store, monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror(-2, "Name or service not known secret.test")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert await _deliver(store) is True
    assert _row(store) == ("queued", 1, "gaierror")


async def test_idna_unicode_error_is_a_contained_retry(store, monkeypatch):
    def boom(*a, **k):
        raise UnicodeError("label too long")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert await _deliver(store) is True
    assert _row(store) == ("queued", 1, "UnicodeError")


async def test_last_error_unknown_subclass_names_collapse_to_category(store):
    class PrivateTokenError(httpx.HTTPError):
        pass

    def h(r):
        raise PrivateTokenError("x")
    await _deliver(store, handler=h)
    assert _row(store)[2] == "HTTPError"


# --- PROTECTION (pass on base) ---
async def test_PROTECTION_public_destination_delivers(store):
    seen = []
    await _deliver(store, seen=seen)
    assert seen == ["https://hooks.example/mee"] and _row(store)[0] == "delivered"


async def test_PROTECTION_private_hosts_override_honored(store, monkeypatch):
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
    monkeypatch.setattr(socket, "getaddrinfo", _dns_to("127.0.0.1"))
    seen = []
    await _deliver(store, seen=seen)
    assert len(seen) == 1 and _row(store)[0] == "delivered"


async def test_PROTECTION_redirects_not_followed(store):
    seen = []

    def h(r):
        seen.append(str(r.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/x"}, request=r)
    c = httpx.AsyncClient(transport=httpx.MockTransport(h), follow_redirects=False)
    await WebhookDispatcher(store, c).deliver_one()
    await c.aclose()
    assert seen == ["https://hooks.example/mee"] and _row(store)[2] == "HTTP 302"


def test_PROTECTION_default_client_does_not_follow_redirects():
    import asyncio
    d = WebhookDispatcher(store=None)
    assert d.client.follow_redirects is False
    asyncio.run(d.client.aclose())

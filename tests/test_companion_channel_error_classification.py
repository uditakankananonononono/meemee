"""Area 218: companion channel error classification and text. MockTransport + monkeypatched DNS only.

LIMIT, verbatim: whether an exception raised mid-request after bytes left is unknown cannot be proven
with MockTransport beyond the class decision. These tests show which class maps to definite vs unknown,
not wire-level behavior. Tests marked PROTECTION already pass on base.
"""
import asyncio
import socket
import time

import httpx
import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.channels import (
    ChannelError,
    DeliveryOutcomeUnknown,
    ProviderChannel,
    WebhookChannel,
)
from meemee.companion.worker import deliver_due_once

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
ADDR = "https://hooks.example/inbox"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)


def raising(exc):
    def h(request):
        raise exc
    return h


def make(kind, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ch = WebhookChannel(client=client) if kind == "webhook" else ProviderChannel(kind, "https://provider.example", "tok", client=client)
    return ch, client


def addr(kind):
    return ADDR if kind == "webhook" else "synthetic-recipient"


KINDS = ["webhook", "whatsapp"]

DEFINITE = [httpx.InvalidURL("https://secret.test/?token=PRIVATE"), UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates")]
UNKNOWN = [httpx.StreamConsumed(), httpx.CookieConflict("c"), httpx.ReadError("r"), httpx.RemoteProtocolError("p")]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("exc", DEFINITE, ids=lambda e: type(e).__name__)
async def test_pre_send_classes_are_definite_channel_errors(kind, exc):
    ch, client = make(kind, raising(exc))
    async with client:
        with pytest.raises(ChannelError) as ei:
            await ch.send(addr(kind), "hi")
    assert not isinstance(ei.value, DeliveryOutcomeUnknown)
    assert "PRIVATE" not in str(ei.value) and "secret.test" not in str(ei.value)
    assert type(exc).__name__ in str(ei.value)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("exc", UNKNOWN, ids=lambda e: type(e).__name__)
async def test_mid_request_classes_are_explicitly_unknown(kind, exc):
    ch, client = make(kind, raising(exc))
    async with client:
        with pytest.raises(DeliveryOutcomeUnknown) as ei:
            await ch.send(addr(kind), "hi")
    assert "unknown" in str(ei.value) and type(exc).__name__ in str(ei.value)


@pytest.mark.parametrize("kind", KINDS)
async def test_unknown_subclass_name_collapses_to_category(kind):
    class PrivateTokenError(httpx.ReadError):
        pass
    ch, client = make(kind, raising(PrivateTokenError("x")))
    async with client:
        with pytest.raises(DeliveryOutcomeUnknown) as ei:
            await ch.send(addr(kind), "hi")
    assert "PrivateToken" not in str(ei.value) and "ReadError" in str(ei.value)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("exc", [httpx.ReadError("https://secret.test/?token=PRIVATE"), httpx.ConnectError("https://secret.test/?token=PRIVATE")])
async def test_error_text_has_no_exception_text(kind, exc):
    ch, client = make(kind, raising(exc))
    async with client:
        with pytest.raises(ChannelError) as ei:
            await ch.send(addr(kind), "hi")
    assert "PRIVATE" not in str(ei.value) and "secret.test" not in str(ei.value)


@pytest.mark.parametrize("exc", [socket.gaierror(-2, "Name or service not known"), UnicodeError("label too long")], ids=["gaierror", "UnicodeError"])
@pytest.mark.parametrize("kind", KINDS)
async def test_dns_failure_before_send_is_definite_with_fixed_text(monkeypatch, kind, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    calls = []
    ch, client = make(kind, lambda r: calls.append(1) or httpx.Response(204, request=r))
    async with client:
        with pytest.raises(ChannelError) as ei:
            await ch.send(addr(kind), "hi")
    assert not isinstance(ei.value, DeliveryOutcomeUnknown)
    assert calls == [] and "hooks.example" not in str(ei.value) and "provider.example" not in str(ei.value)
    assert "could not be resolved" in str(ei.value)


@pytest.mark.parametrize("kind", KINDS)
async def test_dns_does_not_block_event_loop(monkeypatch, kind):
    def slow(*a, **k):
        time.sleep(0.4)
        return PUBLIC
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1
    ch, client = make(kind, lambda r: httpx.Response(204, request=r))
    async with client:
        t = asyncio.create_task(ticker())
        await ch.send(addr(kind), "hi")
        t.cancel()
    assert ticks >= 5


# ---- through the worker (fake transport, no sockets)
async def test_worker_dns_failure_is_retryable_not_unknown(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror(-2, "nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    store, engine, channels = setup(tmp_path)
    ch, client = make("webhook", lambda r: httpx.Response(204, request=r))
    async with client:
        channels["webhook"] = ch
        queue_checkin(store, channel="webhook", address=ADDR)
        proof, nonce = store.create_destination_challenge("udita", ADDR)
        store.consume_destination_challenge("udita", proof["id"], nonce)
        summary = await deliver_due_once(store, engine, channels)
    assert summary["status"] == "queued"
    assert "unknown" not in store.list_checkins("udita")[0]["last_error"]


async def test_PROTECTION_worker_stream_error_is_unknown_and_not_retried(tmp_path):
    store, engine, channels = setup(tmp_path)
    calls = []

    def h(r):
        calls.append(1)
        raise httpx.StreamConsumed()
    ch, client = make("webhook", h)
    async with client:
        channels["webhook"] = ch
        queue_checkin(store, channel="webhook", address=ADDR)
        proof, nonce = store.create_destination_challenge("udita", ADDR)
        store.consume_destination_challenge("udita", proof["id"], nonce)
        summary = await deliver_due_once(store, engine, channels)
        assert summary["status"] == "failed"
        assert "unknown" in store.list_checkins("udita")[0]["last_error"]
        assert await deliver_due_once(store, engine, channels) == {"claimed": False}
    assert calls == [1]


# ---- PROTECTION (pass on base)
@pytest.mark.parametrize("kind", KINDS)
async def test_PROTECTION_status_connect_and_success_paths(kind):
    ch, client = make(kind, lambda r: httpx.Response(503, request=r))
    async with client:
        with pytest.raises(ChannelError, match="HTTP 503"):
            await ch.send(addr(kind), "hi")
    ch, client = make(kind, raising(httpx.ConnectError("x")))
    async with client:
        with pytest.raises(ChannelError) as ei:
            await ch.send(addr(kind), "hi")
        assert not isinstance(ei.value, DeliveryOutcomeUnknown)
    ch, client = make(kind, lambda r: httpx.Response(204, request=r))
    async with client:
        assert (await ch.send(addr(kind), "hi")).delivered is True


@pytest.mark.parametrize("kind", KINDS)
async def test_PROTECTION_private_address_value_error_is_unchanged(monkeypatch, kind):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    ch, client = make(kind, lambda r: httpx.Response(204, request=r))
    async with client:
        with pytest.raises(ValueError, match="private"):
            await ch.send(addr(kind), "hi")

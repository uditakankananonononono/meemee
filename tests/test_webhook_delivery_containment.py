"""Area 215: webhook delivery attempt containment. MockTransport only (no sockets).

Tests marked PROTECTION already pass on base.
"""
import asyncio
import socket
import sqlite3
from pathlib import Path

import httpx
import pytest

from meemee.webhooks import WebhookDispatcher, WebhookStore

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _dns(*a):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    s = WebhookStore(tmp_path / "w.db", encryption_key=KEY)
    yield s
    s.db.close()


def _one(store, event="e1", headers=None):
    ident, _ = store.subscribe("u", "https://hooks.example/mee", {"*"}, headers=headers)
    store.enqueue(event, "job.done", {"ok": True}, principal="u")
    return ident


def _row(store):
    return tuple(store.db.execute("SELECT status,attempts,last_error FROM webhook_deliveries ORDER BY created_at LIMIT 1").fetchone())


def _disp(handler):
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookDispatcher(store=None, client=c), c


async def _run(store, handler):
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await WebhookDispatcher(store, c).deliver_one()
    finally:
        await c.aclose()


def _raising(exc):
    def h(request):
        raise exc
    return h


ESCAPERS = [
    httpx.StreamConsumed(),
    httpx.CookieConflict("c"),
    httpx.InvalidURL("u"),
    UnicodeEncodeError("ascii", "é", 0, 1, "bad"),
    RecursionError("deep"),
]


@pytest.mark.parametrize("exc", ESCAPERS, ids=lambda e: type(e).__name__)
async def test_non_httperror_transport_failure_requeues_with_class_name_only(store, exc):
    _one(store)
    assert await _run(store, _raising(exc)) is True
    status, attempts, err = _row(store)
    assert (status, attempts) == ("queued", 1)
    assert err == type(exc).__name__


async def test_non_latin_custom_header_requeues_not_raises(store):
    _one(store, headers={"X-A": "caf\u00e9\u20ac"})
    assert await _run(store, lambda r: httpx.Response(204, request=r)) is True
    assert _row(store)[0] == "queued"
    assert _row(store)[2] in ("UnicodeEncodeError", "UnicodeError")


async def test_malformed_stored_url_requeues(store):
    ident = _one(store)
    store.db.execute("UPDATE webhook_subscriptions SET url=? WHERE id=?", ("https://ex\x00ample.com/x", ident))
    assert await _run(store, lambda r: httpx.Response(204, request=r)) is True
    assert _row(store)[0] == "queued"
    assert _row(store)[2] == "InvalidURL"


@pytest.mark.parametrize("bad", ["{not json", "[" * 100000 + "]" * 100000, "[1, 2]", '{"X": 5}'])
async def test_corrupt_stored_headers_requeue_not_raise(store, bad):
    ident = _one(store)
    store.db.execute("UPDATE webhook_subscriptions SET headers=? WHERE id=?", (bad, ident))
    assert await _run(store, lambda r: httpx.Response(204, request=r)) is True
    status, attempts, err = _row(store)
    assert (status, attempts) == ("queued", 1)
    assert err.startswith("headers unreadable (")
    assert "not json" not in err


@pytest.mark.parametrize("corrupt", ["enc:v1:!!!notbase64", "enc:v1:" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQQ=="])
async def test_unreadable_secret_requeues_then_fails_through_normal_max_attempts(store, corrupt):
    ident = _one(store)
    store.db.execute("UPDATE webhook_subscriptions SET secret=? WHERE id=?", (corrupt, ident))
    calls = []
    for _ in range(8):
        store.db.execute("UPDATE webhook_deliveries SET next_attempt_at=0 WHERE status='queued'")
        assert await _run(store, lambda r: calls.append(1) or httpx.Response(204, request=r)) is True
    status, attempts, err = _row(store)
    assert (status, attempts) == ("failed", 8)
    assert err == "secret unreadable"
    assert calls == []  # never POSTed without a signature


async def test_dispatch_forever_survives_poison_delivery_and_delivers_next(store):
    from meemee.webhooks import dispatch_forever
    ident = _one(store, "poison")
    store.db.execute("UPDATE webhook_subscriptions SET headers='{bad' WHERE id=?", (ident,))
    _ = store.subscribe("v", "https://hooks.example/other", {"*"})
    store.enqueue("good", "job.done", {}, principal="v")
    seen = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(204, request=r)))
    task = asyncio.create_task(dispatch_forever(store, 0.01, client=client))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if seen:
            break
    assert not task.done() or task.exception() is None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == ["https://hooks.example/other"]


async def test_PROTECTION_store_failure_after_post_is_not_contained_and_replay_keeps_delivery_id(store, monkeypatch):
    _one(store)
    ids = []

    def handler(r):
        ids.append(r.headers["x-meemee-delivery"])
        return httpx.Response(204, request=r)
    monkeypatch.setattr(store, "succeed", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    with pytest.raises(sqlite3.OperationalError):
        await _run(store, handler)
    assert _row(store)[0] == "sending"  # at-least-once: recoverable, not lost
    assert store.recover_stale(9e12) == 1
    monkeypatch.undo()
    assert await _run(store, handler) is True
    assert len(ids) == 2 and ids[0] == ids[1]
    assert _row(store)[0] == "delivered"


# --- PROTECTION (pass on base) ---
async def test_PROTECTION_httperror_and_status_paths(store):
    _one(store)
    assert await _run(store, _raising(httpx.ReadTimeout("slow"))) is True
    assert _row(store) == ("queued", 1, "ReadTimeout")
    store.db.execute("UPDATE webhook_deliveries SET next_attempt_at=0")
    assert await _run(store, lambda r: httpx.Response(503, request=r)) is True
    assert _row(store)[2] == "HTTP 503"
    store.db.execute("UPDATE webhook_deliveries SET next_attempt_at=0")
    assert await _run(store, lambda r: httpx.Response(204, request=r)) is True
    assert _row(store)[0] == "delivered"


async def test_PROTECTION_last_error_never_contains_exception_text(store):
    _one(store)
    await _run(store, _raising(httpx.ConnectError("https://secret.test/?token=PRIVATE")))
    assert "PRIVATE" not in str(_row(store)) and "secret.test" not in str(_row(store))

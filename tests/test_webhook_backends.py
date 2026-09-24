"""Webhook subscriptions and outbox: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.webhooks import WebhookDispatcher, WebhookStore
from meemee_persist_pg.interfaces import WebhookStoreInterface

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
OTHER_KEY = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA="
URL = "https://hooks.example/a"


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setattr("meemee.webhooks.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))])


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path):
    """Yields a factory ``make(key=KEY, max_payload_bytes=...)`` over one shared store location."""
    if request.param == "sqlite":
        yield lambda key=KEY, max_payload_bytes=256_000: WebhookStore(tmp_path / "w.sqlite3", max_payload_bytes, key)
        return
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg import WebhookStore as PGStore

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield lambda key=KEY, max_payload_bytes=256_000: PGStore(db, max_payload_bytes, key)
    finally:
        db.close(); drop()


@pytest.fixture
def store(backend):
    return backend()


def test_interface_subscriptions_are_owner_scoped_and_paged(store):
    assert isinstance(store, WebhookStoreInterface) and store.ping()
    made = [store.subscribe("alice", f"{URL}{i}", {"job.done"}) for i in range(3)]
    store.subscribe("bob", URL, {"*"})
    assert store.active_count("alice") == 3 and store.active_count("bob") == 1
    first, cursor = store.list_subscriptions("alice", limit=2)
    rest, end = store.list_subscriptions("alice", limit=2, cursor=cursor)
    assert end is None and [s["id"] for s in first + rest] == [ident for ident, _ in reversed(made)]
    assert all(set(s) == {"id", "url", "events", "active", "created_at"} and s["active"] == 1 for s in first + rest)
    assert not store.unsubscribe(made[0][0], "bob") and store.unsubscribe(made[0][0], "alice")
    assert store.active_count("alice") == 2
    with pytest.raises(ValueError):
        store.list_subscriptions("alice", cursor="junk")
    with pytest.raises(ValueError, match="forbidden"):
        store.subscribe("alice", URL, {"job.done"}, headers={"Authorization": "x"})
    with pytest.raises(ValueError, match="HTTPS"):
        store.subscribe("alice", "http://hooks.example/a", {"job.done"})


def test_enqueue_is_owner_scoped_filtered_and_deduplicated(store):
    full, _ = store.subscribe("alice", URL, {"job.done"})
    slim, _ = store.subscribe("alice", URL + "2", {"*"}, fields={"job_id"})
    store.subscribe("alice", URL + "3", {"job.failed"})
    store.subscribe("bob", URL, {"*"})
    payload = {"job_id": "j1", "status": "done", "result": {"x": 1}}
    assert store.enqueue("job:j1:done", "job.done", payload, principal="alice") == 2
    assert store.enqueue("job:j1:done", "job.done", payload, principal="alice") == 0
    assert store.list_deliveries("bob")[0] == []
    by_sub = {d["subscription_id"]: d for d in store.list_deliveries("alice")[0]}
    assert set(by_sub) == {full, slim} and all(d["status"] == "queued" and d["attempts"] == 0 for d in by_sub.values())
    claimed = [store.claim(now=time.time() + 1), store.claim(now=time.time() + 1)]
    assert store.claim(now=time.time() + 1) is None
    bodies = {d["subscription_id"]: json.loads(d["payload"]) for d in claimed}
    assert bodies[full]["data"] == payload and bodies[slim]["data"] == {"job_id": "j1"}
    assert all(len(d["secret"]) > 20 and d["url"].startswith(URL) for d in claimed)
    with pytest.raises(ValueError, match="exceeds"):
        store.enqueue("big", "job.done", {"blob": "x" * 300_000}, principal="alice")


def test_delivery_lifecycle_retry_breaker_replay_and_cooldown(store):
    ident, _ = store.subscribe("alice", URL, {"*"})
    store.enqueue("e0", "job.done", {}, principal="alice")
    delivery = store.claim(now=time.time() + 1)
    store.succeed(delivery["id"], 204)
    assert store.get_delivery("alice", delivery["id"])["status"] == "delivered"
    assert store.get_delivery("bob", delivery["id"]) is None
    [attempt] = store.attempt_timeline(delivery["id"], "alice")
    assert attempt["outcome"] == "delivered" and attempt["response_status"] == 204 and attempt["finished_at"]
    assert store.attempt_timeline(delivery["id"], "bob") == []

    for i in range(1, 6):  # five terminal failures trip the breaker
        store.enqueue(f"e{i}", "job.done", {}, principal="alice")
        failing = store.claim(now=time.time() + 1)
        store.retry(failing["id"], "HTTP 500", max_attempts=1)
        assert store.get_delivery("alice", failing["id"])["status"] == "failed"
    assert store.active_count("alice") == 0
    health = store.health(ident, "alice")
    assert health["counts"] == {"queued": 0, "sending": 0, "delivered": 1, "failed": 5} and health["active"] == 0
    assert health["latest"]["last_error"] == "HTTP 500" and store.health(ident, "bob") is None
    assert not store.replay_delivery("alice", failing["id"])  # inactive subscription
    with pytest.raises(ValueError, match="cooldown"):
        store.set_active(ident, "alice", True, cooldown_seconds=3600)
    assert store.set_active(ident, "alice", True) and not store.set_active(ident, "bob", True)
    assert store.replay_delivery("alice", failing["id"]) and not store.replay_delivery("bob", failing["id"])
    again = store.claim(now=time.time() + 1)
    assert again["id"] == failing["id"] and again["attempts"] == 1  # history kept: this is attempt 2
    store.retry(again["id"], "ConnectError")
    queued = store.get_delivery("alice", again["id"])
    assert queued["status"] == "queued" and queued["next_attempt_at"] > time.time() and queued["last_error"] == "ConnectError"
    assert [a["outcome"] for a in store.attempt_timeline(again["id"], "alice")] == ["failed", "queued"]


def test_stale_recovery_cleanup_and_metrics(store):
    store.subscribe("alice", URL, {"*"})
    for i in range(3):
        store.enqueue(f"e{i}", "job.done", {}, principal="alice")
    a, b = store.claim(now=time.time() + 1), store.claim(now=time.time() + 1)
    store.succeed(a["id"], 200)
    assert store.delivery_metrics() == {"queued": 1, "sending": 1, "delivered": 1, "failed": 0}
    assert store.recover_stale(time.time() + 10) == 1
    assert store.delivery_metrics()["queued"] == 2
    metrics = store.operational_metrics()
    assert metrics["success_rate"] == 1.0 and metrics["suspended"] == 0.0 and metrics["oldest_queued_seconds"] >= 0
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert store.cleanup_deliveries(future, future) == {"delivered": 1, "failed": 0}
    assert store.attempt_timeline(a["id"], "alice") == []  # attempts go with their delivery
    assert len(store.attempt_timeline(b["id"], "alice")) == 1


def test_test_event_rotation_and_account_purge(store):
    ident, secret = store.subscribe("alice", URL, {"job.done"})
    assert store.enqueue_test(ident, "bob") is None and store.enqueue_test("missing", "alice") is None
    event_id = store.enqueue_test(ident, "alice")
    [delivery] = store.list_deliveries("alice")[0]
    assert delivery["event_id"] == event_id and delivery["event_type"] == "webhook.test" and delivery["payload_sha256"]
    assert store.rotate_secret(ident, "bob") is None
    rotated = store.rotate_secret(ident, "alice")
    assert rotated and rotated != secret and store.claim(now=time.time() + 1)["secret"] == rotated
    store.subscribe("bob", URL, {"*"})
    assert store.delete_principal("alice") == {"webhook_subscriptions": 1, "webhook_deliveries": 1, "webhook_attempts": 1}
    assert store.list_subscriptions("alice")[0] == [] and store.active_count("bob") == 1


def test_wrong_key_fails_closed(backend):
    backend().subscribe("alice", URL, {"*"})
    with pytest.raises(Exception):  # noqa: B017 - cipher raises its own error type
        backend(OTHER_KEY)
    with pytest.raises(ValueError, match="MEEMEE_VAULT_KEY"):
        backend(None)


def test_concurrent_claims_never_hand_out_a_delivery_twice(backend):
    stores = [backend() for _ in range(4)]  # separate handles, like separate dispatcher processes
    stores[0].subscribe("alice", URL, {"*"})
    for i in range(40):
        stores[0].enqueue(f"e{i}", "job.done", {"i": i}, principal="alice")

    def drain(store):
        got = []
        while (d := store.claim(now=time.time() + 1)) is not None:
            got.append(d["id"])
        return got
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(drain, stores * 2))
    claimed = [ident for batch in results for ident in batch]
    assert len(claimed) == 40 and len(set(claimed)) == 40


def test_dispatcher_signs_and_delivers_from_either_backend(store):
    _, secret = store.subscribe("alice", URL, {"job.done"}, headers={"X-Team": "ops"})
    store.enqueue("job:j:done", "job.done", {"job_id": "j"}, principal="alice")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    async def run():
        dispatcher = WebhookDispatcher(store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        # deliver_one claims with the wall clock; queued rows are due immediately
        return await dispatcher.deliver_one()
    assert asyncio.run(run())
    [request] = seen
    body = request.content.decode()
    assert request.headers["X-Team"] == "ops" and request.headers["X-Meemee-Event"] == "job.done"
    assert request.headers["X-Meemee-Signature-256"] == WebhookDispatcher.signature(secret, request.headers["X-Meemee-Timestamp"], body)
    assert store.list_deliveries("alice", status="delivered")[0][0]["response_status"] == 200

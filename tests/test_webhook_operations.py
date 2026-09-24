import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meemee.webhooks import (
    WebhookStore,
    cleanup_deliveries,
    delivery_metrics,
    list_deliveries,
    replay_delivery,
)

TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def public_dns(*args): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34",443))]


def populated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db", encryption_key=TEST_KEY)
    store.subscribe("u", "https://hooks.example/a", {"*"})
    store.enqueue("e1", "job.done", {"x":1}, principal="u")
    claimed = store.claim(now=4102444800); store.retry(claimed["id"], "bad", max_attempts=1)
    return store, claimed["id"]


def test_owner_list_metrics_and_replay(tmp_path: Path, monkeypatch):
    store, ident = populated(tmp_path, monkeypatch)
    assert list_deliveries(store, "other")[0] == []
    rows = list_deliveries(store, "u", status="failed")[0]
    assert rows[0]["id"] == ident and "payload" not in rows[0] and "secret" not in rows[0]
    assert delivery_metrics(store)["failed"] == 1
    assert replay_delivery(store, "u", ident)
    assert not replay_delivery(store, "other", ident)
    assert delivery_metrics(store)["queued"] == 1


def test_cleanup_only_old_terminal_rows(tmp_path: Path, monkeypatch):
    store, _ident = populated(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc)-timedelta(days=100)).isoformat()
    with store.db: store.db.execute("UPDATE webhook_deliveries SET created_at=?", (old,))
    report = cleanup_deliveries(store, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
    assert report == {"delivered":0,"failed":1}
    assert store.db.execute("SELECT count(*) FROM webhook_deliveries").fetchone()[0] == 0

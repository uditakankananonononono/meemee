import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meemee.webhooks import WebhookStore, operational_metrics


def public_dns(*args): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]


def test_operational_metrics(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",public_dns)
    store=WebhookStore(tmp_path/"w.db"); sid,_=store.subscribe("u","https://hooks.example/a",{"*"})
    for i in range(2): store.enqueue(f"e{i}","job.done",{})
    first=store.claim(now=4102444800); store.succeed(first["id"],204)
    second=store.claim(now=4102444800); store.retry(second["id"],"bad",max_attempts=1)
    store.set_active(sid,"u",False)
    metrics=operational_metrics(store)
    assert metrics["success_rate"]==0.5 and metrics["suspended"]==1.0


def test_oldest_queued_age(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",public_dns)
    store=WebhookStore(tmp_path/"w.db"); store.subscribe("u","https://hooks.example/a",{"*"}); store.enqueue("e","x",{})
    old=(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()
    with store.db: store.db.execute("UPDATE webhook_deliveries SET created_at=?",(old,))
    assert operational_metrics(store)["oldest_queued_seconds"] >= 119

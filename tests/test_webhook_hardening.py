import socket
import time
from pathlib import Path

from meemee.webhooks import WebhookStore


def public_dns(*args): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34",443))]


def test_stale_sending_is_recovered(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db"); store.subscribe("u","https://hooks.example/a",{"*"}); store.enqueue("e","job.done",{})
    claimed = store.claim(now=time.time()+1); assert claimed
    assert store.recover_stale(time.time()+2) == 1
    row = store.db.execute("SELECT status,last_error FROM webhook_deliveries").fetchone()
    assert tuple(row) == ("queued","dispatcher lease expired")


def test_secret_rotation_is_owner_scoped(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store = WebhookStore(tmp_path / "w.db"); ident, old = store.subscribe("u","https://hooks.example/a",{"*"})
    assert store.rotate_secret(ident, "other") is None
    new = store.rotate_secret(ident, "u")
    assert new and new != old
    assert store.db.execute("SELECT secret FROM webhook_subscriptions WHERE id=?",(ident,)).fetchone()[0] == new

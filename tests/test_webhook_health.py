import socket
from pathlib import Path

from meemee.webhooks import WebhookStore


def public_dns(*args): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34",443))]


def setup(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store=WebhookStore(tmp_path/"w.db"); sid,_=store.subscribe("u","https://hooks.example/a",{"*"}); store.enqueue("e","job.done",{}); delivery=store.claim(now=4102444800)
    return store,sid,delivery["id"]


def test_pause_resume_health_and_attempts(tmp_path: Path, monkeypatch):
    store,sid,did=setup(tmp_path,monkeypatch)
    store.retry(did,"HTTP 503",max_attempts=8)
    assert store.attempt_timeline(did,"other")==[]
    attempts=store.attempt_timeline(did,"u"); assert attempts[0]["outcome"]=="queued"
    health=store.health(sid,"u"); assert health["counts"]["queued"]==1
    assert store.set_active(sid,"u",False); assert not store.health(sid,"u")["active"]
    assert store.set_active(sid,"u",True); assert store.health(sid,"u")["active"]


def test_circuit_breaker_suspends_after_five_terminal_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store=WebhookStore(tmp_path/"w.db"); sid,_=store.subscribe("u","https://hooks.example/a",{"*"})
    for i in range(5):
        store.enqueue(f"e{i}","job.failed",{})
        delivery=store.claim(now=4102444800); store.retry(delivery["id"],"permanent",max_attempts=1)
    assert not store.health(sid,"u")["active"]


def test_breaker_cooldown_blocks_early_resume(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    store=WebhookStore(tmp_path/"w.db"); sid,_=store.subscribe("u","https://hooks.example/a",{"*"})
    store.enqueue("e","job.failed",{}); delivery=store.claim(now=4102444800); store.retry(delivery["id"],"bad",max_attempts=1)
    store.set_active(sid,"u",False)
    import pytest
    with pytest.raises(ValueError, match="cooldown"):
        store.set_active(sid,"u",True,cooldown_seconds=300)

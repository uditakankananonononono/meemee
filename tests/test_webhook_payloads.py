import hashlib
import json
import socket
from pathlib import Path

import pytest

from meemee.webhooks import WebhookStore, list_deliveries


def public_dns(*args): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]


def test_envelope_selection_and_hash(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",public_dns)
    store=WebhookStore(tmp_path/"w.db"); store.subscribe("u","https://hooks.example/a",{"job.done"},{"job_id","status"})
    store.enqueue("e1","job.done",{"job_id":"j","status":"done","private":"omit"})
    row=store.db.execute("SELECT payload,payload_sha256 FROM webhook_deliveries").fetchone()
    body=json.loads(row["payload"])
    assert body=={"schema":"meemee.webhook.v1","event_id":"e1","event_type":"job.done","data":{"job_id":"j","status":"done"}}
    assert row["payload_sha256"]==hashlib.sha256(row["payload"].encode()).hexdigest()
    listed=list_deliveries(store,"u")[0]
    assert listed["payload_sha256"]==row["payload_sha256"] and "payload" not in listed


def test_payload_ceiling_fails_before_enqueue(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",public_dns)
    store=WebhookStore(tmp_path/"w.db",max_payload_bytes=1024); store.subscribe("u","https://hooks.example/a",{"*"})
    with pytest.raises(ValueError,match="exceeds"):
        store.enqueue("e","x",{"large":"x"*2000})
    assert store.db.execute("SELECT count(*) FROM webhook_deliveries").fetchone()[0]==0

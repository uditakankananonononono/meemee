import socket

from meemee.audit import AuditLog
from meemee.webhooks import WebhookStore, list_deliveries

KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def test_webhook_delivery_keyset_pages(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a:[(2,1,6,"",("93.184.216.34",443))])
    store=WebhookStore(tmp_path/"w.db",encryption_key=KEY); store.subscribe("u","https://x.example/h",{"*"})
    for i in range(3): store.enqueue(f"e{i}","x",{})
    first,cursor=list_deliveries(store,"u",limit=2); second,end=list_deliveries(store,"u",limit=2,cursor=cursor)
    assert len(first)==2 and len(second)==1 and end is None
    assert {x["id"] for x in first}.isdisjoint({x["id"] for x in second})


def test_audit_page_has_next_sequence_cursor(tmp_path):
    audit=AuditLog(tmp_path/"a.db")
    for i in range(3): audit.append("u","x",str(i),"ok")
    first,cursor=audit.list_page(limit=2); second,end=audit.list_page(after=int(cursor),limit=2)
    assert [x["sequence"] for x in first]==[1,2] and [x["sequence"] for x in second]==[3] and end is None

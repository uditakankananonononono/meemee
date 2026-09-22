from meemee.webhook_verify import verify_signature
from meemee.webhooks import WebhookDispatcher

TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

def test_signature_helper_matches_dispatcher():
    body='{"ok":true}'
    signature=WebhookDispatcher.signature("secret","1000",body)
    assert verify_signature("secret","1000",body.encode(),signature,now=1001)
    assert not verify_signature("wrong","1000",body.encode(),signature,now=1001)


def test_signature_helper_rejects_stale_and_malformed():
    signature=WebhookDispatcher.signature("secret","1000","{}")
    assert not verify_signature("secret","1000",b"{}",signature,now=2000)
    assert not verify_signature("secret","bad",b"{}",signature,now=1000)


def test_custom_headers_reject_sensitive_and_deliver_safe(tmp_path, monkeypatch):
    import socket

    import httpx
    import pytest

    from meemee.webhooks import WebhookDispatcher, WebhookStore
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a:[(2,1,6,"",("93.184.216.34",443))])
    store=WebhookStore(tmp_path/"w.db", encryption_key=TEST_KEY)
    with pytest.raises(ValueError,match="forbidden"):
        store.subscribe("u","https://hooks.example/a",{"*"},headers={"Authorization":"bad"})
    store.subscribe("u","https://hooks.example/a",{"*"},headers={"X-Tenant":"alpha"}); store.enqueue("e","x",{})
    seen={}
    def handler(request): seen.update(request.headers); return httpx.Response(204,request=request)
    import asyncio
    asyncio.run(WebhookDispatcher(store,httpx.AsyncClient(transport=httpx.MockTransport(handler))).deliver_one())
    assert seen["x-tenant"]=="alpha"

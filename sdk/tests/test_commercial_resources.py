import json

import httpx
from conftest import make_client


def test_quota_get_and_set():
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json={"principal":"u","limit":100,"used":2,"remaining":98,"resets_at":"2026-09-23T00:00:00Z"})
    client=make_client(handler)
    assert client.quota.get().remaining==98
    assert client.quota.set("u",200).limit==100
    assert calls==[("GET","/v1/quota"),("PUT","/v1/quota/u")]


def test_webhook_create_list_rotate_and_deliveries():
    def handler(request):
        if request.url.path=="/v1/webhooks" and request.method=="POST":
            body=json.loads(request.content); assert body["events"]==["job.done"]
            return httpx.Response(200,json={"id":"w1","secret":"once","warning":"shown once"})
        if request.url.path=="/v1/webhooks":
            return httpx.Response(200,json={"webhooks":[{"id":"w1","url":"https://x.example/h","events":"job.done","active":1,"created_at":"2026-09-22T00:00:00Z"}]})
        if request.url.path.endswith("rotate-secret"):
            return httpx.Response(200,json={"id":"w1","secret":"twice","warning":"shown once"})
        return httpx.Response(200,json={"deliveries":[]})
    client=make_client(handler)
    assert client.webhooks.create("https://x.example/h",{"job.done"}).secret=="once"
    assert client.webhooks.list()[0].id=="w1"
    assert client.webhooks.rotate_secret("w1").secret=="twice"
    assert client.webhooks.deliveries(status="failed")==[]

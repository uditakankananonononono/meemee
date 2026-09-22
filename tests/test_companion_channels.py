import json
import socket
from pathlib import Path

import httpx
import pytest

from meemee.companion.channels import (
    ChannelError,
    ChannelNotConfiguredError,
    LocalChannel,
    ProviderChannel,
    WebhookChannel,
)
from meemee.companion.store import CompanionStore


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_local_channel_appends_assistant_message(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    conversation = store.start_conversation("udita", "local")
    result = await LocalChannel(store).send(conversation["id"], "check-in text")
    assert result.delivered
    history = store.history(conversation["id"])
    assert history[-1]["role"] == "assistant" and history[-1]["content"] == "check-in text"


@pytest.mark.asyncio
async def test_local_channel_unknown_conversation(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    with pytest.raises(ChannelError):
        await LocalChannel(store).send("missing", "text")


@pytest.mark.asyncio
async def test_webhook_channel_signs_and_delivers():
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        seen["signature"] = request.headers.get("x-meemee-signature", "")
        seen["timestamp"] = request.headers.get("x-meemee-timestamp", "")
        return httpx.Response(200, request=request)

    channel = WebhookChannel(secret="s3cret", client=make_client(handler))
    result = await channel.send("https://hooks.example.com/incoming", "hello there")
    assert result.delivered and result.detail == "HTTP 200"
    assert json.loads(seen["body"])["text"] == "hello there"
    assert seen["signature"].startswith("sha256=") and seen["timestamp"]


@pytest.mark.asyncio
async def test_webhook_channel_unsigned_without_secret():
    def handler(request):
        assert "x-meemee-signature" not in request.headers
        return httpx.Response(202, request=request)

    channel = WebhookChannel(client=make_client(handler))
    result = await channel.send("https://hooks.example.com/incoming", "hi")
    assert result.delivered


@pytest.mark.asyncio
async def test_webhook_channel_rejects_http_and_failure_status():
    channel = WebhookChannel(client=make_client(lambda request: httpx.Response(200, request=request)))
    with pytest.raises(ValueError):
        await channel.send("http://insecure.example.com/hook", "hi")
    failing = WebhookChannel(
        client=make_client(lambda request: httpx.Response(500, request=request))
    )
    with pytest.raises(ChannelError, match="HTTP 500"):
        await failing.send("https://hooks.example.com/incoming", "hi")


@pytest.mark.asyncio
async def test_provider_channel_fails_closed_without_configuration():
    channel = ProviderChannel("whatsapp", None, None, client=make_client(lambda r: httpx.Response(200, request=r)))
    assert not channel.configured()
    with pytest.raises(ChannelNotConfiguredError, match="MEEMEE_WHATSAPP_PROVIDER_URL"):
        await channel.send("+911234567890", "hi")


@pytest.mark.asyncio
async def test_provider_channel_delivers_when_configured():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, request=request)

    channel = ProviderChannel(
        "imessage", "https://provider.example.com/api", "token-123", client=make_client(handler)
    )
    assert channel.configured()
    result = await channel.send("+918134098571", "check-in")
    assert result.delivered
    assert seen["url"] == "https://provider.example.com/api/messages"
    assert seen["auth"] == "Bearer token-123"
    assert seen["body"] == {"to": "+918134098571", "text": "check-in"}


@pytest.mark.asyncio
async def test_provider_channel_surfaces_provider_errors():
    channel = ProviderChannel(
        "whatsapp", "https://provider.example.com", "t",
        client=make_client(lambda request: httpx.Response(429, request=request)),
    )
    with pytest.raises(ChannelError, match="HTTP 429"):
        await channel.send("+1", "hi")

import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel


def ok_handler(text, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]}, request=request)
    return handler


@pytest.mark.asyncio
async def test_chat_returns_free_text():
    client = httpx.AsyncClient(transport=httpx.MockTransport(ok_handler("  hello there  ")))
    model = OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client)
    assert await model.chat([{"role": "user", "content": "hi"}]) == "hello there"


@pytest.mark.asyncio
async def test_chat_sends_temperature_and_max_tokens():
    seen = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(ok_handler("ok", seen)))
    model = OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client)
    await model.chat([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=50)
    import json
    body = json.loads(seen[0].content.decode())
    assert body["temperature"] == 0.2 and body["max_tokens"] == 50
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_chat_retries_transient(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 2:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]}, request=request)

    async def no_sleep(delay):
        pass

    monkeypatch.setattr("meemee.llm.asyncio.sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client)
    assert await model.chat([{"role": "user", "content": "hi"}]) == "recovered"
    assert calls == 2


@pytest.mark.asyncio
async def test_chat_rejects_empty_content():
    client = httpx.AsyncClient(transport=httpx.MockTransport(ok_handler("   ")))
    model = OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client)
    with pytest.raises(ModelError, match="empty chat completion"):
        await model.chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_chat_does_not_retry_bad_request():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, request=request))
    )
    model = OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client)
    with pytest.raises(ModelError, match="1 attempts"):
        await model.chat([{"role": "user", "content": "hi"}])

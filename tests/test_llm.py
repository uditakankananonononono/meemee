import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel


@pytest.mark.asyncio
async def test_model_success():
    def handler(request):
        return httpx.Response(200, json={"choices":[{"message":{"content":"{\"final\":\"done\",\"tool_call\":null}"}}]}, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await OpenAICompatibleModel("https://model.test/v1", "m", "k", client=client).decide([])
    assert result.final == "done"


@pytest.mark.asyncio
async def test_model_retries_transient(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3: return httpx.Response(503, request=request)
        return httpx.Response(200, json={"choices":[{"message":{"content":"{\"final\":\"ok\",\"tool_call\":null}"}}]}, request=request)
    async def no_sleep(delay): pass
    monkeypatch.setattr("meemee.llm.asyncio.sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await OpenAICompatibleModel("https://model.test", "m", "k", max_attempts=3, client=client).decide([])
    assert result.final == "ok" and calls == 3


@pytest.mark.asyncio
async def test_model_does_not_retry_bad_request(monkeypatch):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError, match="1 attempts"):
        await OpenAICompatibleModel("https://model.test", "m", "k", client=client).decide([])
    assert calls == 1


@pytest.mark.asyncio
async def test_model_rejects_malformed_payload():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices":[]}, request=r)))
    with pytest.raises(ModelError, match="invalid decision"):
        await OpenAICompatibleModel("https://model.test", "m", "k", client=client).decide([])

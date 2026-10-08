"""Provider transport and decoder failures surface as ModelError (area 212)."""
import gzip

import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel

GOOD = {"choices": [{"message": {"content": '{"final":"ok","tool_call":null}'}}]}


def _model(handler, attempts=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleModel("https://model.test/v1", "m", "k", max_attempts=attempts, client=client)


async def _call(model, method):
    if method == "decide":
        return await model.decide([{"role": "user", "content": "x"}])
    return await model.chat([{"role": "user", "content": "x"}])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(delay):
        pass

    monkeypatch.setattr("meemee.llm.asyncio.sleep", fast)


def _raiser(error):
    calls = []

    def handler(request):
        calls.append(1)
        raise error

    return handler, calls


NON_RETRYABLE_ERRORS = [
    lambda: httpx.DecodingError("bad body"),
    lambda: httpx.ProxyError("proxy refused"),
    lambda: httpx.UnsupportedProtocol("no scheme"),
    lambda: httpx.LocalProtocolError("bad request framing"),
    lambda: httpx.TooManyRedirects("loop"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["decide", "chat"])
@pytest.mark.parametrize("make", NON_RETRYABLE_ERRORS)
async def test_non_retryable_transport_errors_become_model_error(method, make):
    handler, calls = _raiser(make())
    with pytest.raises(ModelError):
        await _call(_model(handler), method)
    assert len(calls) == 1  # not retried


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["decide", "chat"])
async def test_corrupt_gzip_body_becomes_model_error(method):
    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not gzip", request=request)

    with pytest.raises(ModelError):
        await _call(_model(handler), method)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["decide", "chat"])
@pytest.mark.parametrize("shape", ["array", "object"])
async def test_deeply_nested_provider_json_becomes_model_error(method, shape):
    body = ("[" * 1500 + "0" + "]" * 1500) if shape == "array" else ('{"a":' * 1500 + "0" + "}" * 1500)

    def handler(request):
        return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"}, request=request)

    with pytest.raises(ModelError):
        await _call(_model(handler), method)


# ---- retry semantics and valid behaviour unchanged ----

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["decide", "chat"])
async def test_transient_transport_errors_still_retry_then_succeed(method):
    calls = []
    good = GOOD if method == "decide" else {"choices": [{"message": {"content": "hello"}}]}

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("down", request=request)
        if len(calls) == 2:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=good, request=request)

    result = await _call(_model(handler), method)
    assert len(calls) == 3
    assert (result.final if method == "decide" else result) in {"ok", "hello"}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["decide", "chat"])
async def test_transient_error_exhaustion_still_reports_attempt_count(method):
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ModelError, match="after 2 attempts"):
        await _call(_model(handler, attempts=2), method)


@pytest.mark.asyncio
async def test_valid_gzip_response_still_works():
    import json

    payload = gzip.compress(json.dumps(GOOD).encode())

    def handler(request):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=payload, request=request)

    assert (await _call(_model(handler), "decide")).final == "ok"

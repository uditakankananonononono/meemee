"""Area 214: response-parsing containment for the shared model layer.

Stub transports only (no sockets). Tests marked PROTECTION already pass on base.
"""
import http.client
import json
import urllib.error

import pytest

from meemee import shared_models as sm
from meemee._vendor.instinct_models import InklingLocal, OrnithOpenAICompat, Router
from meemee.config import Settings
from meemee.llm import ModelError

DEEP = "[" * 100000 + "]" * 100000
DEEPOBJ = '{"a":' * 50000 + "1" + "}" * 50000
SECRET_URL = "http://secret-host.internal:9/v1"
SECRET_BODY = "SECRETBODY-token-abc"


def _ok(text):
    return {"choices": [{"message": {"content": text}}]}


def _model(first_transport, second_transport=None, settings=None):
    chain = [OrnithOpenAICompat(SECRET_URL, "o", transport=first_transport)]
    if second_transport:
        chain.append(InklingLocal("http://i/v1", "i", transport=second_transport))
    getattr(sm, "contain_chain", lambda c: None)(chain)  # absent on base: real base behavior
    return sm.SharedLayerModel(settings or Settings(), router=Router(chain))


def _raises(exc):
    def t(u, b, h, to):
        raise exc
    return t


GOOD = lambda u, b, h, t: _ok("fallback ok")

FAILURES = [
    json.JSONDecodeError("bad", "x", 0),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
    RecursionError("deep"),
    http.client.IncompleteRead(b"part"),
    http.client.RemoteDisconnected("gone"),
    urllib.error.URLError("dns " + SECRET_URL),
]


@pytest.mark.parametrize("exc", FAILURES, ids=lambda e: type(e).__name__)
async def test_transport_or_decoder_failure_falls_over_to_next_provider(exc):
    model = _model(_raises(exc), GOOD)
    assert await model.chat([{"role": "user", "content": "x"}]) == "fallback ok"
    assert model.last_provider == "inkling-local"


async def test_malformed_tool_call_arguments_do_not_escape_router():
    bad = lambda u, b, h, t: {"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "f", "arguments": "{not json"}}]}}]}
    model = _model(bad, GOOD)
    assert await model.chat([{"role": "user", "content": "x"}]) == "fallback ok"


async def test_deep_tool_call_arguments_do_not_escape_router():
    bad = lambda u, b, h, t: {"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "f", "arguments": DEEP}}]}}]}
    model = _model(bad, GOOD)
    assert await model.chat([{"role": "user", "content": "x"}]) == "fallback ok"


async def test_all_providers_failing_raises_model_error_not_raw():
    model = _model(_raises(RecursionError("deep")))
    with pytest.raises(ModelError):
        await model.chat([{"role": "user", "content": "x"}])


async def test_model_error_text_has_no_url_body_or_exception_text():
    def leak(u, b, h, t):
        raise urllib.error.HTTPError(u, 500, "boom " + SECRET_BODY, {}, None)
    def leak_ds(u, b, h, t):
        from meemee._vendor.instinct_models.providers import ProviderError
        raise ProviderError(f"HTTP 500 from {u}: {SECRET_BODY!r}")
    for tr in (leak_ds, _raises(urllib.error.URLError("dns " + SECRET_URL))):
        model = _model(tr)
        with pytest.raises(ModelError) as ei:
            await model.chat([{"role": "user", "content": "x"}])
        text = str(ei.value) + json.dumps(model.last_attempts)
        assert "secret-host" not in text and SECRET_BODY not in text and "dns" not in text
    assert "HTTP 500" in str(ei.value) or "unavailable" in str(ei.value) or "error" in str(ei.value)


async def test_http_status_is_kept_in_detail():
    from meemee._vendor.instinct_models.providers import ProviderError
    def t(u, b, h, to):
        raise ProviderError(f"HTTP 503 from {u}: {SECRET_BODY!r}")
    model = _model(t)
    with pytest.raises(ModelError, match="HTTP 503"):
        await model.chat([{"role": "user", "content": "x"}])


async def test_decide_deep_nested_model_output_is_model_error():
    model = _model(lambda u, b, h, t: _ok("here " + DEEPOBJ))
    with pytest.raises(ModelError):
        await model.decide([{"role": "user", "content": "d"}])


async def test_decide_invalid_decision_error_has_no_model_text():
    model = _model(lambda u, b, h, t: _ok('{"action": "SECRETBODY-token-abc"}'))
    with pytest.raises(ModelError) as ei:
        await model.decide([{"role": "user", "content": "d"}])
    assert SECRET_BODY not in str(ei.value)


def test_extract_json_object_deep_nesting_is_model_error():
    from meemee.providers import extract_json_object
    with pytest.raises(ModelError):
        extract_json_object(DEEPOBJ)


def test_PROTECTION_extract_json_object_plain_and_wrapped():
    from meemee.providers import extract_json_object
    assert json.loads(extract_json_object('sure {"a": {"b": 1}} ok')) == {"a": {"b": 1}}


async def test_PROTECTION_happy_path_through_contained_chain():
    model = _model(lambda u, b, h, t: _ok("hello"))
    assert await model.chat([{"role": "user", "content": "x"}]) == "hello"


def test_contain_chain_is_idempotent_and_build_chain_is_contained():
    chain = sm.build_chain(Settings(shared_ornith_url="http://o/v1", shared_ornith_model="o"))
    assert all(getattr(p.chat, "_meemee_contained", False) for p in chain)
    sm.contain_chain(chain)
    p = chain[1]
    assert getattr(p.chat.__wrapped__, "_meemee_contained", False) is False


BYPASS_SHAPES = [
    "transport failure: http://secret.test?token=PRIVATE",
    "ornith: undecodable response SECRET_BODY",
    "ornith-local: transport failure (URLError) http://secret.test?token=PRIVATE",
    "x ornith-local: transport failure (SECRET_BODYError)",
    "ornith-local: undecodable response (SECRET_BODY)",
    "HTTP 500 from http://secret.test?token=PRIVATE: SECRET_BODY",
    "HTTP 500 SECRET_BODY",
    "OpenClaw HTTP 502 SECRET_BODY",
    "prefix\nornith-local: transport failure (URLError)",
    "ornith-local: undecodable response (RecursionError)\nSECRET_BODY",
]


@pytest.mark.parametrize("detail", BYPASS_SHAPES)
async def test_provider_raised_error_text_never_reaches_model_text(detail):
    from meemee._vendor.instinct_models.providers import ProviderError

    def t(*a):
        raise ProviderError(detail)
    model = _model(t)
    with pytest.raises(ModelError) as ei:
        await model.chat([{"role": "user", "content": "x"}])
    observed = str(ei.value) + json.dumps(model.last_attempts)
    for marker in ("PRIVATE", "SECRET_BODY", "secret.test", "token"):
        assert marker not in observed


def test_safe_detail_canonical_forms_only():
    assert sm._safe_detail("error", "HTTP 503 from http://x: y") == "HTTP 503"
    assert sm._safe_detail("error", "ornith-local: transport failure (URLError)") == "transport failure (URLError)"
    assert sm._safe_detail("error", "anything else") == "provider error"
    assert sm._safe_detail("skipped", "not a tool-calling task") == "not a tool-calling task"


async def test_unknown_httpexception_subclass_name_is_not_echoed():
    class SECRET_BODYException(http.client.HTTPException):
        pass
    model = _model(_raises(SECRET_BODYException("x")))
    with pytest.raises(ModelError) as ei:
        await model.chat([{"role": "user", "content": "x"}])
    assert "SECRET_BODY" not in str(ei.value) + json.dumps(model.last_attempts)


async def test_custom_provider_name_is_not_echoed():
    from meemee._vendor.instinct_models.providers import ProviderError

    class Custom(OrnithOpenAICompat):
        name = "SECRET_BODY-name"
    def t(*a):
        raise ProviderError("x")
    chain = [Custom("http://o/v1", "o", transport=t)]
    sm.contain_chain(chain)
    model = sm.SharedLayerModel(Settings(), router=Router(chain))
    with pytest.raises(ModelError) as ei:
        await model.chat([{"role": "user", "content": "x"}])
    assert "SECRET_BODY" not in str(ei.value) + json.dumps(model.last_attempts)


async def test_rethrown_provider_error_subclass_with_canonical_prefix_and_secret():
    from meemee._vendor.instinct_models.providers import ProviderUnavailable

    def t(*a):
        raise ProviderUnavailable("ornith-local: transport failure (URLError) http://secret.test?token=PRIVATE")
    model = _model(t)
    with pytest.raises(ModelError) as ei:
        await model.chat([{"role": "user", "content": "x"}])
    observed = str(ei.value) + json.dumps(model.last_attempts)
    assert "PRIVATE" not in observed and "secret.test" not in observed


@pytest.mark.parametrize("cls", ["PrivateTokenError", "SecretHostError", "ABCError", "RecursionErrorX"])
async def test_alphabetic_secret_class_name_in_canonical_form_not_echoed(cls):
    from meemee._vendor.instinct_models.providers import ProviderError

    def t(*a):
        raise ProviderError(f"ornith-local: undecodable response ({cls})")
    model = _model(t)
    with pytest.raises(ModelError) as ei:
        await model.chat([{"role": "user", "content": "x"}])
    observed = str(ei.value) + json.dumps(model.last_attempts)
    assert cls not in observed
    assert "provider error" in observed


def test_safe_detail_only_allowlisted_class_names_roundtrip():
    for c in sm._CLASS_NAMES:
        assert sm._safe_detail("error", f"ornith-local: transport failure ({c})") == f"transport failure ({c})"
    assert sm._safe_detail("error", "ornith-local: transport failure (PrivateTokenError)") == "provider error"

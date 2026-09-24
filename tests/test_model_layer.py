"""Provider abstraction: local-first default, HF router fallback, in-process transformers path."""
import importlib.util
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from meemee import providers
from meemee.config import Settings
from meemee.llm import ModelError, OpenAICompatibleModel
from meemee.model_profiles import (
    ModelCatalog,
    RoutedModel,
    build_role_model,
    default_chain,
    probe_profile,
    uses_routing,
)
from meemee.providers import TransformersModel, extract_json_object

ROUTER = "https://router.huggingface.co/v1/chat/completions"
LOCAL = "http://local.test/v1"
HAVE_TORCH = importlib.util.find_spec("transformers") is not None and importlib.util.find_spec("torch") is not None


def settings(**kw):
    return Settings(_env_file=None, **kw)


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture(autouse=True)
def _fresh_cache():
    providers.clear_cache()
    yield
    providers.clear_cache()


# ---- routing defaults --------------------------------------------------------------------

def test_default_route_is_local_only_without_hf_token():
    s = settings()
    assert default_chain(s) == ["local"]
    assert not uses_routing(s)
    assert isinstance(build_role_model(s, "agent"), OpenAICompatibleModel)


def test_hf_token_adds_inkling_as_fallback_after_local_for_every_role():
    s = settings(hf_token="hf_x")
    assert default_chain(s) == ["local", "inkling"]
    cat = ModelCatalog.from_settings(s)
    assert cat.routes == {r: ["local", "inkling"] for r in ("agent", "chat", "reflection")}
    m = build_role_model(s, "chat")
    assert isinstance(m, RoutedModel) and m.role == "chat"


def test_hf_fallback_can_be_turned_off_and_explicit_routes_win():
    assert default_chain(settings(hf_token="hf_x", hf_fallback=False)) == ["local"]
    cat = ModelCatalog.from_settings(settings(hf_token="hf_x", model_routes="chat=local"))
    assert cat.routes["chat"] == ["local"]
    assert cat.routes["agent"] == ["local", "inkling"]  # unlisted roles keep the default chain


@respx.mock
async def test_local_down_falls_back_to_hf_router_with_inkling_request_shape():
    s = settings(hf_token="hf_test", model_base_url=LOCAL)
    respx.post(f"{LOCAL}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    router = respx.post(ROUTER).mock(return_value=completion('{"final": "from inkling"}'))
    m = build_role_model(s, "agent")
    m.max_attempts = 1
    decision = await m.decide([{"role": "user", "content": "hi"}])
    assert decision.final == "from inkling"
    sent = router.calls.last.request
    assert sent.headers["authorization"] == "Bearer hf_test"
    body = json.loads(sent.content)
    assert body["model"] == "thinkingmachines/Inkling-Small"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert [a["profile"] for a in m.attempts] == ["local", "inkling"]
    assert m.attempts[0]["outcome"].startswith("failed") and m.attempts[1]["outcome"] == "ok"
    await m.aclose()


@respx.mock
async def test_exhausted_hf_credits_is_a_clean_failure_not_a_retry_loop():
    s = settings(hf_token="hf_test", model_base_url=LOCAL)
    respx.post(f"{LOCAL}/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    router = respx.post(ROUTER).mock(return_value=httpx.Response(402, json={"error": "credits exhausted"}))
    m = build_role_model(s, "chat")
    with pytest.raises(ModelError, match="all model profiles failed"):
        await m.chat([{"role": "user", "content": "hi"}])
    assert router.call_count == 1  # 402 is not retried
    assert "402" in m.attempts[-1]["outcome"]
    await m.aclose()


# ---- transformers profile wiring ----------------------------------------------------------

def test_local_transformers_is_builtin_free_and_local():
    cat = ModelCatalog.from_settings(settings(transformers_model="org/tiny"))
    p = cat.profiles["local-transformers"]
    assert (p.kind, p.transport, p.model, p.requires_key, p.paid) == ("local", "transformers", "org/tiny", False, False)
    assert p.public_dict(False)["transport"] == "transformers"
    assert cat.profiles["local"].public_dict(False)["transport"] == "openai"


def test_transformers_profile_unavailable_when_libraries_missing(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n in {"torch", "transformers"} else real(n, *a))
    cat = ModelCatalog.from_settings(settings(model_routes="agent=local-transformers,local"))
    usable, skipped = cat.chain("agent")
    assert [p.name for p in usable] == ["local"]
    assert "pip install 'meemee[transformers]'" in skipped["local-transformers"]


def test_custom_transformers_profile_needs_no_base_url_and_bad_transport_is_rejected():
    raw = json.dumps([{"name": "mine", "model": "/models/qwen", "kind": "local", "transport": "transformers"}])
    cat = ModelCatalog.from_settings(settings(model_profiles=raw))
    assert cat.profiles["mine"].transport == "transformers" and cat.profiles["mine"].base_url == ""
    with pytest.raises(ValueError, match="unknown transport"):
        ModelCatalog.from_settings(settings(model_profiles=json.dumps([{"name": "x", "model": "m", "transport": "grpc"}])))
    with pytest.raises(ValueError, match="base_url"):
        ModelCatalog.from_settings(settings(model_profiles=json.dumps([{"name": "x", "model": "m"}])))


# ---- TransformersModel with a fake loader (no torch needed) ----------------------------------

class _Ids(list):
    @property
    def shape(self):
        return (1, len(self))

    def to(self, device):
        return self


class _Tok:
    chat_template = None
    pad_token_id = 0
    eos_token_id = 0

    def __call__(self, text, return_tensors=None):
        self.prompt = text
        return {"input_ids": _Ids(range(len(text.split())))}

    def decode(self, tokens, skip_special_tokens=True):
        return "".join(tokens)


class _Model:
    device = "cpu"

    def __init__(self, reply):
        self.reply = reply
        self.kwargs = None

    def generate(self, input_ids, **kwargs):
        self.kwargs = kwargs
        return [list(input_ids) + list(self.reply)]


def _fake(reply, tok=None):
    tok = tok or _Tok()
    model = _Model(reply)
    return tok, model, (lambda model_id, device: (tok, model))


async def test_transformers_chat_uses_role_transcript_without_chat_template():
    tok, model, loader = _fake(" Paris.")
    m = TransformersModel("fake/model", "cpu", 32, loader=loader)
    out = await m.chat([{"role": "system", "content": "be brief"}, {"role": "user", "content": "capital of France?"}], temperature=0)
    assert out == "Paris."
    assert tok.prompt.endswith("user: capital of France?\nassistant:")
    assert model.kwargs["do_sample"] is False and model.kwargs["max_new_tokens"] == 32


async def test_transformers_decide_extracts_json_wrapped_in_prose():
    _, model, loader = _fake('Sure! {"thought": "done", "final": "42 {not json}"} hope that helps')
    decision = await TransformersModel("fake/json", "cpu", loader=loader).decide([{"role": "user", "content": "q"}])
    assert decision.final == "42 {not json}"
    assert model.kwargs["do_sample"] is False  # decisions are greedy


async def test_transformers_bad_output_and_load_failure_raise_model_error():
    _, _, loader = _fake("no json here")
    with pytest.raises(ModelError, match="no JSON object"):
        await TransformersModel("fake/a", "cpu", loader=loader).decide([{"role": "user", "content": "q"}])
    _, _, loader = _fake('{"final": "a", "tool_call": {"name": "x", "arguments": {}}}')
    with pytest.raises(ModelError, match="invalid decision"):
        await TransformersModel("fake/b", "cpu", loader=loader).decide([{"role": "user", "content": "q"}])

    def boom(model_id, device):
        raise OSError("disk gone")

    with pytest.raises(ModelError, match="could not load"):
        await TransformersModel("fake/c", "cpu", loader=boom).chat([{"role": "user", "content": "q"}])


def test_extract_json_object_handles_braces_inside_strings():
    assert json.loads(extract_json_object('x {"a": "}{", "b": {"c": 1}} y')) == {"a": "}{", "b": {"c": 1}}
    assert extract_json_object('{bad} then {"ok": true}') == '{"ok": true}'


async def test_default_loader_refuses_to_download_mid_request(monkeypatch):
    monkeypatch.setattr(providers, "transformers_missing", lambda: None)
    monkeypatch.setattr(providers, "weights_available", lambda model_id: False)
    with pytest.raises(ModelError, match="meemee models pull"):
        await TransformersModel("org/not-pulled", "cpu").chat([{"role": "user", "content": "q"}])


@respx.mock
async def test_route_falls_from_local_server_to_in_process_transformers(monkeypatch):
    _, _, loader = _fake('{"final": "answered in-process"}')
    monkeypatch.setattr(providers, "_default_loader", loader)
    s = settings(model_base_url=LOCAL, model_routes="agent=local,local-transformers", transformers_model="fake/tiny")
    respx.post(f"{LOCAL}/chat/completions").mock(side_effect=httpx.ConnectError("ollama not running"))
    m = build_role_model(s, "agent")
    m.max_attempts = 1
    decision = await m.decide([{"role": "user", "content": "hi"}])
    assert decision.final == "answered in-process"
    assert [a["profile"] for a in m.attempts][-1] == "local-transformers"
    await m.aclose()


async def test_probe_transformers_profile_reports_weights_on_disk_without_network(monkeypatch):
    monkeypatch.setattr(providers, "transformers_missing", lambda: None)
    cat = ModelCatalog.from_settings(settings())
    monkeypatch.setattr(providers, "weights_available", lambda model_id: False)
    r = await probe_profile(cat.profiles["local-transformers"])
    assert r["reachable"] is False and "meemee models pull local-transformers" in r["error"]
    monkeypatch.setattr(providers, "weights_available", lambda model_id: True)
    r = await probe_profile(cat.profiles["local-transformers"])
    assert r["reachable"] is True and r["served"] == [cat.profiles["local-transformers"].model]


def test_cli_pull_rejects_http_profiles_and_unknown_names(monkeypatch):
    from meemee.cli import app

    monkeypatch.setenv("MEEMEE_DATA_DIR", "/tmp/meemee-pull-test")
    runner = CliRunner()
    r = runner.invoke(app, ["models", "pull", "inkling"])
    assert r.exit_code != 0 and "served over HTTP" in r.output
    r = runner.invoke(app, ["models", "pull", "nope"])
    assert r.exit_code != 0 and "unknown profile" in r.output


# ---- real weights (skipped unless transformers + torch are installed and weights pulled) -------

TINY = "hf-internal-testing/tiny-random-LlamaForCausalLM"
SMOL = "HuggingFaceTB/SmolLM2-135M-Instruct"


def _pulled(model_id):
    return HAVE_TORCH and providers.weights_available(model_id)


@pytest.mark.skipif(not _pulled(TINY), reason=f"needs transformers, torch and `meemee models pull` of {TINY}")
async def test_real_tiny_random_llama_generates_in_process_on_cpu():
    m = TransformersModel(TINY, "cpu", max_new_tokens=8)
    out = await m.chat([{"role": "user", "content": "hello"}], temperature=0)
    assert isinstance(out, str) and out


@pytest.mark.skipif(not _pulled(SMOL), reason=f"needs transformers, torch and `meemee models pull` of {SMOL}")
async def test_real_instruct_model_answers_through_the_route(monkeypatch):
    s = settings(model_base_url="http://127.0.0.1:9/v1", model_routes="chat=local,local-transformers",
                 transformers_model=SMOL, transformers_device="cpu", transformers_max_new_tokens=24)
    m = build_role_model(s, "chat")
    m.max_attempts = 1
    out = await m.chat([{"role": "user", "content": "What is the capital of France? Answer in one word."}], temperature=0)
    assert "paris" in out.lower()
    assert m.attempts[-1] == {"profile": "local-transformers", "outcome": "ok"}
    await m.aclose()


def test_fugu_defaults_to_the_real_sakana_alias_and_reads_sakana_key(monkeypatch):
    monkeypatch.delenv("SAKANA_API_KEY", raising=False)
    p = ModelCatalog.from_settings(settings()).profiles["fugu"]
    assert (p.model, p.base_url, p.kind, p.api_key) == ("fugu-ultra", "https://api.sakana.ai/v1", "hosted_paid", None)
    monkeypatch.setenv("SAKANA_API_KEY", "sk_sakana")
    cat = ModelCatalog.from_settings(settings(model_routes="chat=fugu,local"))
    assert cat.profiles["fugu"].api_key == "sk_sakana"
    assert "MEEMEE_ALLOW_PAID_MODELS" in cat.chain("chat")[1]["fugu"]  # key alone never enables a paid profile
    assert ModelCatalog.from_settings(settings(fugu_api_key="sk_meemee")).profiles["fugu"].api_key == "sk_meemee"

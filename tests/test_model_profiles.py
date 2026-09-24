import json

import httpx
import pytest
import respx

from meemee.config import Settings
from meemee.llm import ModelError
from meemee.model_profiles import ModelCatalog, RoutedModel, parse_routes, probe_profile


def settings(**kw):
    return Settings(_env_file=None, **kw)


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_default_is_local_only_and_free():
    cat = ModelCatalog.from_settings(settings())
    assert cat.routes == {"agent": ["local"], "chat": ["local"], "reflection": ["local"]}
    assert set(cat.profiles) >= {"local", "inkling", "inkling-large", "inkling-vllm", "fugu"}
    assert "ultron" not in cat.profiles
    usable, skipped = cat.chain("agent")
    assert [p.name for p in usable] == ["local"] and skipped == {}


def test_fugu_needs_key_and_paid_opt_in():
    cat = ModelCatalog.from_settings(settings(model_routes="chat=fugu,local"))
    usable, skipped = cat.chain("chat")
    assert [p.name for p in usable] == ["local"]
    assert skipped["fugu"] == "no API key configured"
    cat = ModelCatalog.from_settings(settings(model_routes="chat=fugu,local", fugu_api_key="k"))
    assert "paid profile" in cat.chain("chat")[1]["fugu"]
    cat = ModelCatalog.from_settings(settings(model_routes="chat=fugu,local", fugu_api_key="k", allow_paid_models=True))
    assert [p.name for p in cat.chain("chat")[0]] == ["fugu", "local"]


def test_route_parsing_errors():
    with pytest.raises(ValueError):
        parse_routes("planner=local")
    with pytest.raises(ValueError):
        parse_routes("agent")
    with pytest.raises(ValueError):
        ModelCatalog.from_settings(settings(model_routes="agent=ultron"))


def test_custom_profiles_from_json_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    f = tmp_path / "profiles.json"
    f.write_text(json.dumps([{"name": "groq-free", "base_url": "https://x.test/v1/", "model": "m",
                              "kind": "hosted_free", "api_key_env": "MY_KEY"}]))
    cat = ModelCatalog.from_settings(settings(model_profiles=str(f), model_routes="agent=groq-free,local"))
    p = cat.profiles["groq-free"]
    assert p.base_url == "https://x.test/v1" and p.api_key == "secret"
    assert [x.name for x in cat.chain("agent")[0]] == ["groq-free", "local"]
    with pytest.raises(ValueError):
        ModelCatalog.from_settings(settings(model_profiles='[{"name":"a"}]'))


@respx.mock
async def test_fallback_to_next_profile_on_failure():
    s = settings(model_routes="agent=inkling-vllm,local", inkling_base_url="http://ink.test/v1",
                 model_base_url="http://local.test/v1")
    respx.post("http://ink.test/v1/chat/completions").mock(return_value=httpx.Response(400))
    respx.post("http://local.test/v1/chat/completions").mock(return_value=completion('{"final": "done"}'))
    m = RoutedModel(ModelCatalog.from_settings(s), "agent", max_attempts=1)
    d = await m.decide([{"role": "user", "content": "hi"}])
    assert d.final == "done" and m.last_profile == "local"
    assert m.attempts[0]["profile"] == "inkling-vllm" and m.attempts[0]["outcome"].startswith("failed")
    await m.aclose()


@respx.mock
async def test_chat_uses_first_healthy_and_sends_model_id():
    s = settings(model_routes="chat=inkling", hf_token="hf_test")
    route = respx.post("https://router.huggingface.co/v1/chat/completions").mock(return_value=completion("hello"))
    m = RoutedModel(ModelCatalog.from_settings(s), "chat", max_attempts=1)
    assert await m.chat([{"role": "user", "content": "hi"}]) == "hello"
    req = route.calls[0].request
    assert json.loads(req.content)["model"] == "thinkingmachines/Inkling-Small"
    assert req.headers["authorization"] == "Bearer hf_test"
    await m.aclose()


@respx.mock
async def test_all_fail_raises_with_every_reason():
    s = settings(model_routes="agent=fugu,local", model_base_url="http://local.test/v1")
    respx.post("http://local.test/v1/chat/completions").mock(return_value=httpx.Response(401))
    m = RoutedModel(ModelCatalog.from_settings(s), "agent", max_attempts=1)
    with pytest.raises(ModelError, match="all model profiles failed"):
        await m.decide([{"role": "user", "content": "hi"}])
    assert m.attempts[0] == {"profile": "fugu", "outcome": "skipped: no API key configured"}
    await m.aclose()


async def test_no_usable_profile():
    s = settings(model_routes="agent=fugu")
    m = RoutedModel(ModelCatalog.from_settings(s), "agent")
    with pytest.raises(ModelError, match="no usable model profile"):
        await m.decide([])


@respx.mock
async def test_probe_reports_listing_without_completion():
    s = settings(model_base_url="http://local.test/v1")
    respx.get("http://local.test/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": s.model_name}, {"id": "other"}]}))
    r = await probe_profile(ModelCatalog.from_settings(s).profiles["local"])
    assert r["reachable"] and r["model_listed"]
    respx.get("http://ink.test/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    s2 = settings(inkling_base_url="http://ink.test/v1")
    r2 = await probe_profile(ModelCatalog.from_settings(s2).profiles["inkling-vllm"])
    assert r2["reachable"] is False and "ConnectError" in r2["error"]


def test_runtime_wires_routed_model_only_when_configured(tmp_path):
    from meemee.llm import OpenAICompatibleModel
    from meemee.runtime import build_agent

    plain = build_agent(settings(data_dir=tmp_path / "a", workspace=tmp_path), include_delegation=False)
    assert isinstance(plain.model, OpenAICompatibleModel)
    routed = build_agent(
        settings(data_dir=tmp_path / "b", workspace=tmp_path, model_routes="agent=inkling-vllm,local"),
        include_delegation=False,
    )
    assert isinstance(routed.model, RoutedModel)
    assert routed.model.catalog.routes["agent"] == ["inkling-vllm", "local"]


def test_companion_and_reflection_roles_follow_routes(tmp_path):
    from meemee.companion.runtime import build_companion
    from meemee.model_profiles import build_role_model

    c = build_companion(settings(data_dir=tmp_path, model_routes="chat=inkling,local", hf_token="hf_x"))
    assert isinstance(c.model, RoutedModel) and c.model.role == "chat"
    r = build_role_model(settings(model_routes="reflection=inkling,local"), "reflection")
    assert isinstance(r, RoutedModel) and r.catalog.routes["reflection"] == ["inkling", "local"]
    with pytest.raises(ValueError):
        build_role_model(settings(), "planner")


def test_inkling_hf_needs_token_then_is_free_tier_not_paid_gated():
    cat = ModelCatalog.from_settings(settings(model_routes="chat=inkling,local"))
    assert cat.chain("chat")[1] == {"inkling": "no API key configured"}
    cat = ModelCatalog.from_settings(settings(model_routes="chat=inkling,inkling-large,local", hf_token="hf_x"))
    usable = [p.name for p in cat.chain("chat")[0]]
    assert usable == ["inkling", "inkling-large", "local"]
    assert cat.profiles["inkling-large"].model == "thinkingmachines/Inkling"


@respx.mock
async def test_inkling_hf_rejected_token_falls_back_to_local():
    s = settings(model_routes="chat=inkling,local", hf_token="hf_bad", model_base_url="http://local.test/v1")
    respx.post("https://router.huggingface.co/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "Invalid username or password."}))
    respx.post("http://local.test/v1/chat/completions").mock(return_value=completion("local reply"))
    m = RoutedModel(ModelCatalog.from_settings(s), "chat", max_attempts=1)
    assert await m.chat([{"role": "user", "content": "hi"}]) == "local reply"
    assert m.attempts[0]["profile"] == "inkling" and "401" in m.attempts[0]["outcome"]
    await m.aclose()


@pytest.mark.skipif(not __import__("os").environ.get("MEEMEE_LIVE_HF"), reason="live HF router check is opt-in")
async def test_live_hf_router_lists_inkling_and_answers_when_token_set():
    s = Settings(_env_file=None, hf_token=__import__("os").environ.get("MEEMEE_HF_TOKEN"))
    cat = ModelCatalog.from_settings(s)
    probe = await probe_profile(cat.profiles["inkling"], timeout=20)
    assert probe["reachable"] and probe["model_listed"]
    if s.hf_token:
        m = RoutedModel(ModelCatalog.from_settings(Settings(_env_file=None, hf_token=s.hf_token,
                                                            model_routes="chat=inkling")), "chat")
        assert (await m.chat([{"role": "user", "content": "Reply with one word: ok"}], max_tokens=5)).strip()
        await m.aclose()

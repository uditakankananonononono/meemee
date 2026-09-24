import json

import pytest

from meemee import shared_models as sm
from meemee._vendor.instinct_models import OrnithOpenAICompat, Router
from meemee.config import Settings
from meemee.llm import ModelError
from meemee.model_profiles import ModelCatalog, builtin_profiles


def test_vendor_pin_matches_shared_models_commit():
    pin = sm.vendor_pin()
    assert pin["repo"] == "https://github.com/uditakankananonononono/shared-models"
    assert len(pin["commit"]) == 40


def test_shared_profile_unavailable_until_a_generation_model_is_configured():
    p = builtin_profiles(Settings())["shared"]
    assert p.transport == "instinct"
    assert "MEEMEE_SHARED_ORNITH_URL" in (p.unavailable_reason(False) or "")
    ok = builtin_profiles(Settings(shared_ornith_url="http://o/v1", shared_ornith_model="ornith"))["shared"]
    assert ok.unavailable_reason(False) is None


def test_hosted_router_only_when_opted_in():
    names = [p.name for p in sm.build_chain(Settings())]
    assert "inkling-hf-router" not in names
    names = [p.name for p in sm.build_chain(Settings(shared_allow_hosted=True, hf_token="hf_x"))]
    assert names[-1] == "inkling-hf-router"


def _reply(content):
    return {"choices": [{"message": {"content": content}}]}


async def test_shared_model_decide_and_chat_through_router():
    decision = {"thought": "done", "action": "final", "final": "hi"}
    seen = []
    orn = OrnithOpenAICompat("http://o/v1", "ornith", transport=lambda u, b, h, t: seen.append(b) or _reply(
        "sure: " + json.dumps(decision) if b["messages"][-1]["content"] == "decide" else "hello"))
    model = sm.SharedLayerModel(Settings(), router=Router([orn]))
    out = await model.decide([{"role": "user", "content": "decide"}])
    assert out.model_dump(exclude_none=True).get("final", out.model_dump().get("final")) is not None
    assert model.last_provider == "ornith-local"
    assert await model.chat([{"role": "user", "content": "x"}]) == "hello"


async def test_shared_model_raises_model_error_with_attempts():
    model = sm.SharedLayerModel(Settings(), router=Router([OrnithOpenAICompat(None, None)]))
    with pytest.raises(ModelError, match="ornith-local: unavailable"):
        await model.chat([{"role": "user", "content": "x"}])


def test_meemee_dataset_trains_only_on_confirmed_meemee_rows(tmp_path):
    tools = [{"name": "add_reminder", "parameters": {"type": "object", "properties": {"what": {"type": "string"}}}}]
    rows = [
        {"query": "remind me to call mom", "tools": tools, "answers": [{"name": "add_reminder", "arguments": {"what": "call mom"}}], "confirmed": True},
        {"query": "what's the weather", "tools": tools, "answers": [], "confirmed": True},
        {"query": "Acme owes a report", "tools": tools, "answers": [], "confirmed": True, "product": "atlas"},
        {"query": "remind me to pay rent", "tools": tools, "answers": [{"name": "add_reminder", "arguments": {"what": "pay rent"}}], "confirmed": False},
    ]
    ex = tmp_path / "ex.jsonl"
    ex.write_text("\n".join(json.dumps(r) for r in rows))
    cmds = []

    def runner(cmd, env):
        cmds.append(cmd)
        if cmd[1] == "build":
            (tmp_path / "out" / "meemee-tuned.cact").write_bytes(b"x")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")

    res = sm.train_meemee_needle(ex, tmp_path / "out", epochs=3, runner=runner)
    assert (res["manifest"]["rows"], res["manifest"]["dropped"]) == (2, 2)
    assert res["record"]["product"] == "meemee"
    assert [c[1] for c in cmds] == ["finetune", "build"]


def test_catalog_route_config_accepts_shared_profile():
    cat = ModelCatalog.from_settings(Settings(model_routes="chat=shared,local",
                                              shared_ornith_url="http://o/v1", shared_ornith_model="o"))
    usable, _ = cat.chain("chat")
    assert usable[0].name == "shared"

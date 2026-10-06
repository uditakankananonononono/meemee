"""No configured generation model must mean unavailable, not a fabricated completion."""
import pytest

from meemee.agent import Agent
from meemee.companion.engine import CompanionEngine
from meemee.companion.store import CompanionStore
from meemee.config import Settings
from meemee.context import ContextRecord, ContextStore
from meemee.llm import ModelError
from meemee.memory import MemoryStore
from meemee.model_profiles import build_role_model
from meemee.personal_model import PersonalModelStore
from meemee.reflection import PersonalModelReflector
from meemee.tools.base import ToolRegistry


@pytest.fixture
def no_model(tmp_path):
    # Explicitly select the unconfigured shared local-only provider, so no network
    # or accidental ambient hosted key can satisfy this test.
    settings = Settings(data_dir=tmp_path, model_routes="agent=shared;chat=shared", hf_token=None,
                        shared_ornith_url=None, shared_inkling_url=None, shared_hermes_url=None,
                        shared_needle_weights=None, shared_allow_hosted=False)
    return build_role_model(settings, "agent")


@pytest.mark.asyncio
async def test_agent_without_generation_weights_does_not_store_fake_final(tmp_path, no_model):
    memory = MemoryStore(tmp_path / "memory.db")
    with pytest.raises(ModelError, match="no usable model"):
        await Agent(no_model, ToolRegistry(), memory).run("find opportunities", owner_id="a")
    assert [row["kind"] for row in memory.recent(owner_id="a")] == ["goal"]
    await no_model.aclose()


@pytest.mark.asyncio
async def test_companion_without_generation_weights_does_not_fake_chat(tmp_path, no_model):
    store = CompanionStore(tmp_path / "companion.db")
    engine = CompanionEngine(no_model, store)
    with pytest.raises(ModelError, match="no usable model"):
        await engine.reply("a", "hello")
    conversation = store.latest_conversation("a", "local")
    assert [row["role"] for row in store.history(conversation["id"])] == ["user"]
    assert store.list_facts("a") == []
    await no_model.aclose()


@pytest.mark.asyncio
async def test_reflection_without_generation_weights_creates_no_personal_claims(tmp_path, no_model):
    context = ContextStore(tmp_path / "context.db")
    personal = PersonalModelStore(tmp_path / "personal.db")
    context.register_source("a", "feed", "rss", {})
    context.ingest(ContextRecord("a", "feed", "r", "document", "Preference",
                                 "I prefer quiet libraries", "2026-10-01T00:00:00Z", {}))
    with pytest.raises(ModelError, match="no usable model"):
        await PersonalModelReflector(context, personal, no_model).reflect("a")
    assert personal.list("a") == []
    await no_model.aclose()

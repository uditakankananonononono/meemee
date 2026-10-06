import json
import os
import uuid
from pathlib import Path

import pytest

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.semantic_memory import HashingEmbedder, MiniLMEmbedder
from meemee.tools.base import ToolRegistry
from meemee.types import AgentDecision


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path):
    if request.param == "sqlite":
        memory = MemoryStore(tmp_path / "memory.db", HashingEmbedder())
        yield memory
        memory.connection.close()
        return
    dsn = os.getenv("MEEMEE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires real PostgreSQL")
    import psycopg
    from psycopg.conninfo import make_conninfo

    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.memory import MemoryStore as PGMemory
    name = "memory_owners_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    db = Database(make_conninfo(dsn, dbname=name))
    try:
        MigrationStore(db).apply()
        yield PGMemory(db, HashingEmbedder())
    finally:
        db.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_all_retrieval_methods_filter_before_ranking(store):
    alice = store.add("a", "fact", "private telescope discovery", owner_id="alice")
    bob = store.add("b", "fact", "private telescope discovery", owner_id="bob")
    legacy = store.add("legacy", "fact", "private telescope discovery")
    for method in (store.search, store.semantic_search, store.hybrid_search):
        assert [row["id"] for row in method("telescope discovery", 10, owner_id="alice")] == [alice]
        assert [row["id"] for row in method("telescope discovery", 10, owner_id="bob")] == [bob]
        assert [row["id"] for row in method("telescope discovery", 10)] == [legacy]
        assert method("telescope discovery", 10, owner_id="charlie") == []
    assert [row["id"] for row in store.recent(owner_id="alice")] == [alice]


@pytest.mark.asyncio
async def test_agent_never_places_other_owner_memory_in_model_prompt(store):
    store.add("b", "fact", "telescope PRIVATE_BOB_CANARY", owner_id="bob")
    store.add("a", "fact", "telescope ALICE_CANARY", owner_id="alice")
    class Capture:
        async def decide(self, messages):
            self.messages = messages
            return AgentDecision(final="owner response")
    model = Capture()
    report = await Agent(model, ToolRegistry(), store).run("telescope", owner_id="alice")
    assert "PRIVATE_BOB_CANARY" not in json.dumps(model.messages)
    assert "ALICE_CANARY" in json.dumps(model.messages)
    assert report.final == "owner response"
    assert {row["run_id"] for row in store.recent(owner_id="alice")} == {"a", report.run_id}
    assert {row["run_id"] for row in store.recent(owner_id="bob")} == {"b"}


def test_sqlite_legacy_owner_migration_quarantines_old_rows(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript("""CREATE TABLE memories(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,
        kind TEXT NOT NULL,content TEXT NOT NULL,metadata TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
        INSERT INTO memories VALUES(1,'old','fact','legacy private secret','{}','2026-01-01');""")
    connection.close()
    memory = MemoryStore(path)
    assert memory.search("legacy", owner_id="alice") == []
    assert memory.recent()[0]["id"] == 1
    assert memory.semantic_search("legacy")[0]["id"] == 1


def test_learned_semantic_memory_is_owner_scoped(store):
    path = os.getenv("MEEMEE_TEST_EMBEDDING_DIR")
    if not path:
        pytest.skip("requires actual MiniLM weights")
    store.embedder = MiniLMEmbedder(Path(path))
    store.add("b", "fact", "My automobile needs fixing", owner_id="bob")
    assert store.semantic_search("The car requires repairs", owner_id="alice") == []
    ident = store.add("a", "fact", "My automobile needs fixing", owner_id="alice")
    assert store.semantic_search("The car requires repairs", owner_id="alice")[0]["id"] == ident


@pytest.mark.asyncio
async def test_delegation_tool_propagates_owner_to_child_memory(tmp_path):
    from meemee.team import AgentTeam
    from meemee.tools.delegate import DelegateTasks
    from meemee.types import ToolCall
    memory = MemoryStore(tmp_path / "delegated.db")
    class ChildModel:
        async def decide(self, messages):
            return AgentDecision(final="child response")
    team = AgentTeam(lambda: Agent(ChildModel(), ToolRegistry(), memory))
    tools = ToolRegistry()
    tools.register(DelegateTasks(lambda: team))
    class ParentModel:
        def __init__(self): self.calls = 0
        async def decide(self, messages):
            self.calls += 1
            if self.calls == 1:
                return AgentDecision(tool_call=ToolCall(name="agents.delegate", arguments={"goals": ["child goal"]}))
            return AgentDecision(final="parent response")
    report = await Agent(ParentModel(), tools, memory).run("parent goal", owner_id="alice")
    assert report.tool_results[0]["result"]["ok"]
    assert any(row["content"] == "child goal" for row in memory.recent(owner_id="alice"))
    assert memory.recent() == []
    assert memory.recent(owner_id="bob") == []


def test_cli_runtime_uses_configured_trained_encoder(tmp_path):
    path = os.getenv("MEEMEE_TEST_EMBEDDING_DIR")
    if not path:
        pytest.skip("requires actual MiniLM weights")
    from meemee.config import Settings
    from meemee.runtime import build_agent
    settings = Settings(data_dir=tmp_path, workspace=tmp_path, embedding_model_dir=Path(path))
    agent = build_agent(settings, include_delegation=False)
    assert isinstance(agent.memory.embedder, MiniLMEmbedder)

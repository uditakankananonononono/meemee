from pathlib import Path

import pytest

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.planner import TaskPlanner
from meemee.tools.base import ToolRegistry
from meemee.tools.filesystem import WriteFile
from meemee.types import AgentDecision, Plan, PlanStep, ToolCall


class FakeModel:
    def __init__(self, decisions): self.decisions = iter(decisions)
    async def decide(self, messages): return next(self.decisions)


def test_planner_dependencies():
    plan = TaskPlanner().plan("research agents; then compare them; finally report")
    assert len(plan.steps) == 3
    assert plan.steps[2].depends_on == ["step-2"]


def test_plan_rejects_unknown_dependency():
    with pytest.raises(ValueError):
        Plan(goal="x", steps=[PlanStep(id="1", description="x", depends_on=["missing"])])


def test_decision_requires_one_action():
    with pytest.raises(ValueError): AgentDecision(thought="x")
    with pytest.raises(ValueError): AgentDecision(tool_call=ToolCall(name="x"), final="done")


def test_memory_add_search_recent(tmp_path: Path):
    memory = MemoryStore(tmp_path / "m.db")
    ident = memory.add("run", "fact", "Python agents are useful", {"source": "test"})
    assert ident > 0
    assert memory.search("Python agents")[0]["metadata"]["source"] == "test"
    assert memory.recent(1)[0]["kind"] == "fact"


@pytest.mark.asyncio
async def test_agent_finishes(tmp_path: Path):
    model = FakeModel([AgentDecision(final="done")])
    agent = Agent(model, ToolRegistry(), MemoryStore(tmp_path / "m.db"))
    report = await agent.run("say done")
    assert report.final == "done" and report.steps_used == 1


@pytest.mark.asyncio
async def test_agent_executes_approved_write(tmp_path: Path):
    registry = ToolRegistry(); registry.register(WriteFile(tmp_path))
    model = FakeModel([
        AgentDecision(tool_call=ToolCall(name="workspace.write_file", arguments={"path":"x","content":"yes"})),
        AgentDecision(final="written"),
    ])
    report = await Agent(model, registry, MemoryStore(tmp_path / "m.db")).run(
        "write", approve=lambda *_: True
    )
    assert report.tool_results[0]["result"]["ok"]
    assert (tmp_path / "x").read_text() == "yes"


@pytest.mark.asyncio
async def test_agent_denies_unapproved_write(tmp_path: Path):
    registry = ToolRegistry(); registry.register(WriteFile(tmp_path))
    model = FakeModel([
        AgentDecision(tool_call=ToolCall(name="workspace.write_file", arguments={"path":"x","content":"no"})),
        AgentDecision(final="blocked"),
    ])
    report = await Agent(model, registry, MemoryStore(tmp_path / "m.db")).run("write")
    assert "approval denied" in report.tool_results[0]["result"]["error"]
    assert not (tmp_path / "x").exists()


@pytest.mark.asyncio
async def test_agent_step_limit(tmp_path: Path):
    decisions = [AgentDecision(tool_call=ToolCall(name="missing")) for _ in range(2)]
    report = await Agent(FakeModel(decisions), ToolRegistry(), MemoryStore(tmp_path / "m.db"), max_steps=2).run("loop")
    assert "Stopped after 2" in report.final

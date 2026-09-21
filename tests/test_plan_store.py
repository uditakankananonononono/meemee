from pathlib import Path

import pytest

from meemee.plan_store import PlanStore
from meemee.types import Plan, PlanStep


def sample() -> Plan:
    return Plan(goal="ship", steps=[PlanStep(id="build", description="build"), PlanStep(id="test", description="test", depends_on=["build"])])


def test_plan_versions_and_history(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.db")
    created = store.create(sample())
    assert created["version"] == 1
    updated = store.update_status(created["id"], "build", "done", 1)
    assert updated["version"] == 2
    assert updated["plan"].steps[0].status == "done"
    assert [h["reason"] for h in store.history(created["id"])] == ["created", "status build -> done"]


def test_plan_optimistic_conflict(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.db")
    created = store.create(sample())
    store.update_status(created["id"], "build", "running", 1)
    with pytest.raises(ValueError, match="version conflict"):
        store.update_status(created["id"], "build", "done", 1)


def test_plan_forward_dependency_is_valid():
    plan = Plan(goal="x", steps=[PlanStep(id="test", description="t", depends_on=["build"]), PlanStep(id="build", description="b")])
    assert plan.steps[0].depends_on == ["build"]


def test_plan_cycle_is_rejected():
    with pytest.raises(ValueError, match="cycle"):
        Plan(goal="x", steps=[PlanStep(id="a", description="a", depends_on=["b"]), PlanStep(id="b", description="b", depends_on=["a"])])

"""Every refused tool call is reported in RunReport.approvals_required, and stored with the run."""
from __future__ import annotations

import json

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.policy import PolicyEngine
from meemee.runs import RunStore
from meemee.tools import ReadFile, ToolRegistry, WriteFile
from meemee.types import AgentDecision


class Script:
    """Model that replays fixed decisions (test double for the model endpoint)."""

    def __init__(self, *decisions: dict):
        self.decisions = [AgentDecision.model_validate(d) for d in decisions]

    async def decide(self, _messages):
        return self.decisions.pop(0)


def _agent(tmp_path, *decisions, policy=None) -> Agent:
    registry = ToolRegistry()
    registry.register(ReadFile(tmp_path))
    registry.register(WriteFile(tmp_path))
    return Agent(Script(*decisions), registry, MemoryStore(tmp_path / "m.sqlite3"), policy=policy)


WRITE = {"tool_call": {"name": "workspace.write_file", "arguments": {"path": "a.txt", "content": "hi"}}}


async def test_unapproved_write_is_listed_with_the_grant_that_would_allow_it(tmp_path):
    report = await _agent(tmp_path, WRITE, {"final": "All done!"}).run("write a file")
    assert report.final == "All done!"  # the model's words can be wrong; the list is the truth
    [refusal] = report.approvals_required
    assert refusal.step == 1 and refusal.tool == "workspace.write_file" and refusal.risk == "write"
    assert refusal.reason == "approval_required" and refusal.grantable is True
    assert refusal.per_run == {"approved_tools": ["workspace.write_file"]}
    assert refusal.persistent_grant == {"tool": "workspace.write_file",
                                        "argument_constraints": {"path": "a.txt", "content": "hi"}}
    assert not (tmp_path / "a.txt").exists()


async def test_approved_write_leaves_the_list_empty(tmp_path):
    report = await _agent(tmp_path, WRITE, {"final": "ok"}).run("write", approve=lambda *_: True)
    assert report.approvals_required == [] and (tmp_path / "a.txt").read_text() == "hi"


async def test_policy_denial_is_listed_as_not_grantable(tmp_path):
    policy = PolicyEngine({"deny_tools": ["workspace.write_file"]})
    report = await _agent(tmp_path, WRITE, {"final": "done"}, policy=policy).run("write", approve=lambda *_: True)
    [refusal] = report.approvals_required
    assert refusal.reason == "policy_denied" and refusal.grantable is False
    assert refusal.per_run is None and refusal.persistent_grant is None


async def test_secret_and_long_arguments_are_not_pinned_in_the_grant(tmp_path):
    big = "x" * 600
    call = {"tool_call": {"name": "workspace.write_file", "arguments": {"path": "b.txt", "content": big}}}
    report = await _agent(tmp_path, call, {"final": "done"}).run("write big")
    assert report.approvals_required[0].persistent_grant["argument_constraints"] == {"path": "b.txt"}


async def test_refusals_survive_the_run_store_and_old_databases(tmp_path):
    report = await _agent(tmp_path, WRITE, {"final": "done"}).run("write")
    store = RunStore(tmp_path / "runs.sqlite3")
    store.add("p", report)
    stored = store.get("p", report.run_id)
    assert stored["approvals_required"][0]["tool"] == "workspace.write_file"
    # a v1 database without the column upgrades in place and old rows read as []
    import sqlite3

    old = tmp_path / "old.sqlite3"
    db = sqlite3.connect(old)
    db.executescript("""CREATE TABLE runs(run_id TEXT PRIMARY KEY, principal TEXT NOT NULL, goal TEXT NOT NULL,
        final TEXT NOT NULL, steps_used INTEGER NOT NULL, tool_results TEXT NOT NULL, created_at TEXT NOT NULL);""")
    db.execute("INSERT INTO runs VALUES('r1','p','g','f',1,?, '2026-01-01T00:00:00+00:00')", (json.dumps([]),))
    db.commit()
    db.close()
    upgraded = RunStore(old)
    assert upgraded.get("p", "r1")["approvals_required"] == []
    upgraded.add("p", report)
    assert upgraded.get("p", report.run_id)["approvals_required"][0]["reason"] == "approval_required"

from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from meemee.team import AgentTeam
from meemee.types import RunReport
from meemee.vault import SecretVault


def test_vault_encrypts_and_retrieves(tmp_path: Path):
    key = SecretVault.generate_key()
    vault = SecretVault(tmp_path / "vault.db", key)
    vault.put("github", "super-secret")
    assert vault.get("github") == "super-secret"
    assert vault.names() == ["github"]
    assert b"super-secret" not in (tmp_path / "vault.db").read_bytes()


def test_vault_wrong_key_fails(tmp_path: Path):
    vault = SecretVault(tmp_path / "vault.db", SecretVault.generate_key())
    vault.put("x", "secret")
    wrong = SecretVault(tmp_path / "vault.db", SecretVault.generate_key())
    with pytest.raises(InvalidTag):
        wrong.get("x")


class FakeAgent:
    async def run(self, goal):
        return RunReport(run_id=goal, goal=goal, final=goal.upper(), steps_used=1, tool_results=[])


@pytest.mark.asyncio
async def test_team_preserves_order():
    reports = await AgentTeam(FakeAgent, max_concurrency=2).delegate(["a", "b"])
    assert [r["report"]["final"] for r in reports] == ["A", "B"]

@pytest.mark.asyncio
async def test_team_propagates_cancellation_to_children():
    from threading import Event
    seen=[]
    class Child:
        async def run(self,goal,cancel=None):
            seen.append(cancel)
            from meemee.types import RunReport
            return RunReport(run_id=goal,goal=goal,final="done",steps_used=1,tool_results=[])
    cancel=Event()
    reports=await AgentTeam(Child,max_concurrency=1).delegate(["a","b"],cancel=cancel)
    assert all(item["ok"] for item in reports) and seen==[cancel,cancel]

@pytest.mark.asyncio
async def test_team_refuses_new_child_after_cancel():
    from threading import Event
    cancel=Event(); cancel.set()
    reports=await AgentTeam(FakeAgent,max_concurrency=1).delegate(["a"],cancel=cancel)
    assert reports[0]["error"]=="delegation cancelled"

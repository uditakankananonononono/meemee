"""A caller-supplied model is not owned by a team child."""

import httpx

from meemee.agent import Agent
from meemee.llm import OpenAICompatibleModel
from meemee.memory import MemoryStore
from meemee.team import AgentTeam
from meemee.tools.base import ToolRegistry
from meemee.types import RunReport


async def test_team_keeps_borrowed_actual_model_client_open(tmp_path):
    client = httpx.AsyncClient()
    model = OpenAICompatibleModel('http://unused', 'model', 'key', client=client)
    child = Agent(model, ToolRegistry(), MemoryStore(tmp_path / 'memory.db'))

    async def run(goal, *, owner_id):
        return RunReport(run_id='run', goal=goal, final='done', steps_used=0, tool_results=[])

    child.run = run
    try:
        assert (await AgentTeam(lambda: child).delegate(['work'], owner_id='owner'))[0]['ok']
        assert not client.is_closed
    finally:
        await client.aclose()

"""A sibling error cannot interrupt another child's already-started cleanup."""

import asyncio
from types import SimpleNamespace

import pytest

from meemee.agent import Agent
from meemee.llm import OpenAICompatibleModel
from meemee.memory import MemoryStore
from meemee.team import AgentTeam
from meemee.tools.base import ToolRegistry


async def test_team_sibling_error_waits_for_started_tool_cleanup(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    events = []
    children = []

    class SlowTool:
        name = 'slow'
        async def aclose(self):
            events.append('tool-close-enter')
            entered.set()
            await release.wait()
            events.append('tool-close-done')

    def factory():
        tools = ToolRegistry()
        if not children:
            tools.register(SlowTool())
        model = OpenAICompatibleModel('http://unused', 'model', 'key')
        child = Agent(model, tools, MemoryStore(tmp_path / f'{len(children)}.db'), owns_model=True, owns_tools=True)
        children.append(child)
        async def run(goal, *, owner_id):
            if goal == 'bad':
                await entered.wait()
                asyncio.get_running_loop().call_later(.02, release.set)
                raise TypeError('original error')
            return SimpleNamespace(model_dump=dict)
        child.run = run
        return child

    try:
        with pytest.raises(TypeError, match='original error'):
            await AgentTeam(factory).delegate(['good', 'bad'], owner_id='owner')
        assert events.count('tool-close-done') == 1
        assert all(child.model.client.is_closed for child in children)
    finally:
        release.set()
        for child in children:
            await child.model.aclose()


async def test_team_waits_for_actual_httpx_transport_release(tmp_path):
    import httpx

    from meemee.tools.github import GitHubRepoSearch

    entered, release = asyncio.Event(), asyncio.Event()
    released = []

    class Transport(httpx.AsyncBaseTransport):
        async def aclose(self):
            entered.set()
            await release.wait()
            released.append('transport')

    tool = GitHubRepoSearch()
    await tool.client._transport.aclose()
    tool.client._transport = Transport()
    registry = ToolRegistry()
    registry.register(tool)
    model = OpenAICompatibleModel('http://unused', 'model', 'key')
    child = Agent(model, registry, MemoryStore(tmp_path / 'actual.db'), owns_model=True, owns_tools=True)

    async def run(goal, *, owner_id):
        return SimpleNamespace(model_dump=dict)

    child.run = run

    class Bad:
        async def run(self, goal, *, owner_id):
            await entered.wait()
            asyncio.get_running_loop().call_later(.02, release.set)
            raise TypeError('original')

    children = iter([Bad(), child])
    try:
        with pytest.raises(TypeError, match='original'):
            await AgentTeam(lambda: next(children)).delegate(['bad', 'slow'], owner_id='owner')
        assert released == ['transport']
        assert tool.client.is_closed
        await child.aclose()
        assert released == ['transport']
    finally:
        release.set()
        await child.aclose()

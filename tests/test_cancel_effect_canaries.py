"""Cancellation must be checked before invoking any tool, including sync tools."""
import asyncio
from threading import Event

import pytest
from pydantic import BaseModel

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.tools.base import Tool, ToolRegistry
from meemee.types import AgentDecision, ToolCall


class Args(BaseModel):
    pass


class Effect(Tool):
    name = 'effect'
    description = 'test effect'
    arguments_model = Args
    calls = 0
    def run(self, arguments):
        self.calls += 1
        return 'mutated'


@pytest.mark.asyncio
async def test_already_cancelled_registry_never_invokes_sync_tool():
    tool, registry, cancel = Effect(), ToolRegistry(), Event()
    registry.register(tool)
    cancel.set()
    result = await registry.execute('effect', {}, cancel=cancel, owner_id='owner')
    assert tool.calls == 0
    assert not result.ok and 'cancelled' in result.error


@pytest.mark.asyncio
async def test_cancel_arriving_during_model_decision_prevents_tool(tmp_path):
    tool, registry, cancel = Effect(), ToolRegistry(), Event()
    registry.register(tool)
    class Model:
        async def decide(self, messages):
            cancel.set()
            await asyncio.sleep(0)
            return AgentDecision(tool_call=ToolCall(name='effect'))
    report = await Agent(Model(), registry, MemoryStore(tmp_path / 'memory.db')).run(
        'do work', owner_id='owner', cancel=cancel)
    assert tool.calls == 0
    assert report.final.startswith('Cancelled')
    assert report.tool_results == []


@pytest.mark.asyncio
async def test_outer_registry_cancellation_cleans_running_child():
    cleaned = asyncio.Event()
    began = asyncio.Event()
    class Slow(Tool):
        name = 'slow'
        description = 'slow'
        arguments_model = Args
        async def run(self, arguments):
            began.set()
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.set()
    registry = ToolRegistry()
    registry.register(Slow())
    task = asyncio.create_task(registry.execute('slow', {}, cancel=Event(), owner_id='owner'))
    await began.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert cleaned.is_set(), 'cancelled registry left tool task running'


@pytest.mark.asyncio
async def test_cancel_stops_model_wait_without_waiting_for_generation(tmp_path):
    began, cleaned, cancel = asyncio.Event(), asyncio.Event(), Event()
    class SlowModel:
        async def decide(self, messages):
            began.set()
            try:
                await asyncio.sleep(10)
                return AgentDecision(final='late answer')
            finally:
                cleaned.set()
    agent = Agent(SlowModel(), ToolRegistry(), MemoryStore(tmp_path / 'memory.db'))
    task = asyncio.create_task(agent.run('work', owner_id='owner', cancel=cancel))
    await began.wait()
    cancel.set()
    try:
        report = await asyncio.wait_for(task, 0.5)
    except asyncio.TimeoutError:
        pytest.fail('cancellation waits for model generation timeout')
    assert cleaned.is_set()
    assert report.final.startswith('Cancelled')

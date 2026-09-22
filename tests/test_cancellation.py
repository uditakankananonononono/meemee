from pathlib import Path
from threading import Event

import pytest

from meemee.agent import Agent
from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.tools.base import ToolRegistry
from meemee.types import AgentDecision


class Model:
    async def decide(self, messages): return AgentDecision(final="should not run")


@pytest.mark.asyncio
async def test_agent_honors_cancel_before_model(tmp_path: Path):
    cancel = Event(); cancel.set()
    report = await Agent(Model(), ToolRegistry(), MemoryStore(tmp_path / "m.db")).run("work", cancel=cancel)
    assert report.steps_used == 0 and report.final.startswith("Cancelled")


def test_running_job_cancel_protocol(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work")
    jobs.claim()
    assert jobs.request_cancel(ident) == "cancel_requested"
    assert jobs.cancel_running(ident)
    assert jobs.get(ident)["status"] == "cancelled"
    assert [event["kind"] for event in jobs.events(ident)][-2:] == ["cancel_requested", "cancelled"]


def test_queued_job_cancel_is_terminal(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work")
    assert jobs.request_cancel(ident) == "cancelled"
    assert jobs.claim() is None


def test_repeated_cancel_is_idempotent_state(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    ident = jobs.enqueue("work")
    assert jobs.request_cancel(ident) == "cancelled"
    assert jobs.request_cancel(ident) == "cancelled"
    assert [e["kind"] for e in jobs.events(ident)].count("cancelled") == 1

@pytest.mark.asyncio
async def test_cancellation_interrupts_running_async_tool(tmp_path: Path):
    import asyncio

    from pydantic import BaseModel

    from meemee.tools.base import Tool
    from meemee.types import ToolCall
    class Args(BaseModel): pass
    class Slow(Tool):
        name="slow"; description="slow"; arguments_model=Args
        def __init__(self): self.cleaned=False
        async def run(self,arguments):
            try: await asyncio.sleep(10)
            finally: self.cleaned=True
    class Calls:
        def __init__(self): self.calls=0
        async def decide(self,messages):
            self.calls+=1
            return AgentDecision(tool_call=ToolCall(name="slow")) if self.calls==1 else AgentDecision(final="done")
    registry=ToolRegistry(); slow=Slow(); registry.register(slow)
    cancel=Event()
    async def trigger(): await asyncio.sleep(.1); cancel.set()
    task=asyncio.create_task(Agent(Calls(),registry,MemoryStore(tmp_path/"m.db")).run("work",cancel=cancel))
    await trigger(); report=await task
    assert slow.cleaned and report.tool_results[0]["result"]["error"]=="tool cancelled"
    assert report.final.startswith("Cancelled")

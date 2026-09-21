from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from threading import Event

from .llm import Model
from .memory import MemoryStore
from .planner import TaskPlanner
from .tools.base import ToolRegistry
from .types import Risk, RunReport

Approval = Callable[[str, dict, Risk], bool]

SYSTEM = """You are Meemee, an honest autonomous agent. Work toward the user's goal with the
available tools. Never claim an action succeeded unless a tool result proves it. Respond only as
JSON: {"thought":"brief reason","tool_call":{"name":"...","arguments":{...}},"final":null}
or {"thought":"brief reason","tool_call":null,"final":"answer"}. Use one tool per turn.
Do not repeat a failed call unchanged. Stop when done or when blocked and name the blocker."""


class Agent:
    def __init__(self, model: Model, tools: ToolRegistry, memory: MemoryStore, max_steps: int = 12):
        self.model = model
        self.tools = tools
        self.memory = memory
        self.max_steps = max_steps
        self.planner = TaskPlanner()

    async def run(self, goal: str, approve: Approval | None = None, cancel: Event | None = None) -> RunReport:
        run_id = uuid.uuid4().hex
        plan = self.planner.plan(goal)
        prior = self.memory.search(goal, limit=5)
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({
                "goal": goal,
                "plan": plan.model_dump(),
                "tools": self.tools.schemas(),
                "relevant_memory": prior,
            }, default=str)},
        ]
        self.memory.add(run_id, "goal", goal, {"plan": plan.model_dump()})
        events: list[dict] = []
        for step_number in range(1, self.max_steps + 1):
            if cancel is not None and cancel.is_set():
                final = "Cancelled before the next agent step."
                self.memory.add(run_id, "cancelled", final)
                return RunReport(run_id=run_id, goal=goal, final=final, steps_used=step_number - 1, tool_results=events)
            decision = await self.model.decide(messages)
            messages.append({"role": "assistant", "content": decision.model_dump_json()})
            if decision.final is not None:
                self.memory.add(run_id, "final", decision.final)
                return RunReport(run_id=run_id, goal=goal, final=decision.final,
                                 steps_used=step_number, tool_results=events)
            call = decision.tool_call
            assert call is not None
            try:
                tool = self.tools.get(call.name)
            except KeyError as exc:
                result = {"ok": False, "error": str(exc)}
            else:
                if tool.risk != Risk.READ and (approve is None or not approve(call.name, call.arguments, tool.risk)):
                    result = {"ok": False, "error": f"approval denied for {tool.risk.value} tool"}
                else:
                    result = (await self.tools.execute(call.name, call.arguments)).model_dump()
            event = {"tool": call.name, "arguments": call.arguments, "result": result}
            events.append(event)
            self.memory.add(run_id, "tool", json.dumps(event, default=str))
            messages.append({"role": "tool", "content": json.dumps(event, default=str)})
        final = f"Stopped after {self.max_steps} steps without a final answer. Last tool event: {events[-1] if events else 'none'}"
        self.memory.add(run_id, "limit", final)
        return RunReport(run_id=run_id, goal=goal, final=final, steps_used=self.max_steps, tool_results=events)

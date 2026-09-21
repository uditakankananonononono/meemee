from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    """Strict model response. Exactly one of final or tool_call is allowed."""

    thought: str = ""
    tool_call: ToolCall | None = None
    final: str | None = None

    @model_validator(mode="after")
    def one_action(self) -> AgentDecision:
        if (self.tool_call is None) == (self.final is None):
            raise ValueError("provide exactly one of tool_call or final")
        return self


class ToolResult(BaseModel):
    ok: bool
    content: Any = None
    error: str | None = None
    elapsed_ms: int = 0


class PlanStep(BaseModel):
    id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "done", "failed"] = "pending"


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]

    @model_validator(mode="after")
    def valid_graph(self) -> Plan:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        known: set[str] = set()
        for step in self.steps:
            if any(dep not in known for dep in step.depends_on):
                raise ValueError(f"{step.id} has unknown or forward dependency")
            known.add(step.id)
        return self


class RunReport(BaseModel):
    run_id: str
    goal: str
    final: str
    steps_used: int
    tool_results: list[dict[str, Any]]

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReplanRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    steps: list[str] = Field(min_length=1, max_length=8)


class AgentDecision(BaseModel):
    """Strict model response. Exactly one of final or tool_call is allowed."""

    thought: str = ""
    tool_call: ToolCall | None = None
    final: str | None = None
    replan: ReplanRequest | None = None

    @model_validator(mode="after")
    def one_action(self) -> AgentDecision:
        if sum(value is not None for value in (self.tool_call, self.final, self.replan)) != 1:
            raise ValueError("provide exactly one of tool_call, replan or final")
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
        known = set(ids)
        for step in self.steps:
            if any(dep not in known for dep in step.depends_on):
                raise ValueError(f"{step.id} has unknown dependency")
            if step.id in step.depends_on:
                raise ValueError(f"{step.id} depends on itself")
        graph = {step.id: step.depends_on for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("plan contains dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in ids:
            visit(node)
        return self


class ApprovalRefusal(BaseModel):
    """A tool call the run was not allowed to make. Present means the goal may be incomplete."""

    step: int
    tool: str
    risk: str
    reason: Literal["approval_required", "policy_denied"]
    detail: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    grantable: bool
    # Exact bodies that would have allowed this call; None when policy forbids it outright.
    per_run: dict[str, Any] | None = None
    persistent_grant: dict[str, Any] | None = None


class RunReport(BaseModel):
    run_id: str
    goal: str
    final: str
    steps_used: int
    tool_results: list[dict[str, Any]]
    approvals_required: list[ApprovalRefusal] = Field(default_factory=list)

    @computed_field  # serialized, so every stored or returned report carries it
    @property
    def blocked(self) -> bool:
        """True when any tool call was refused: the goal may not be done, whatever `final` says."""
        return bool(self.approvals_required)

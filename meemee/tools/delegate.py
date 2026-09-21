from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from ..team import AgentTeam
from .base import Tool


class DelegateArgs(BaseModel):
    goals: list[str] = Field(min_length=1, max_length=8)


class DelegateTasks(Tool):
    name = "agents.delegate"
    description = "Run independent sub-goals concurrently with isolated agent instances."
    arguments_model = DelegateArgs

    def __init__(self, team_factory: Callable[[], AgentTeam]):
        self.team_factory = team_factory

    async def run(self, arguments: DelegateArgs):
        return await self.team_factory().delegate(arguments.goals)

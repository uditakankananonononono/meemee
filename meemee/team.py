from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent


class AgentTeam:
    """Runs independent delegated goals concurrently and preserves per-agent reports."""

    def __init__(self, factory: Callable[[], Agent], max_concurrency: int = 4):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.factory = factory
        self.limit = asyncio.Semaphore(max_concurrency)

    async def delegate(self, goals: list[str]) -> list[dict[str, Any]]:
        if not goals or len(goals) > 32:
            raise ValueError("delegate between 1 and 32 goals")

        async def one(index: int, goal: str) -> dict[str, Any]:
            async with self.limit:
                try:
                    report = await self.factory().run(goal)
                    return {"index": index, "goal": goal, "ok": True, "report": report.model_dump()}
                except (OSError, ValueError, RuntimeError) as exc:
                    return {"index": index, "goal": goal, "ok": False, "error": str(exc)}

        return await asyncio.gather(*(one(index, goal) for index, goal in enumerate(goals)))

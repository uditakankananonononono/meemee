from __future__ import annotations

import asyncio
import inspect
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ValidationError

from ..types import Risk, ToolResult


class Tool(ABC):
    name: str
    description: str
    risk: Risk = Risk.READ
    arguments_model: type[BaseModel]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "parameters": self.arguments_model.model_json_schema(),
        }

    @abstractmethod
    async def run(self, arguments: BaseModel) -> Any: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any], cancel=None) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = self.get(name)
            parsed = tool.arguments_model.model_validate(arguments)
            try:
                parameters = inspect.signature(tool.run).parameters
                value = tool.run(parsed, cancel=cancel) if "cancel" in parameters else tool.run(parsed)
            except (TypeError, ValueError):
                value = tool.run(parsed)
            if inspect.isawaitable(value):
                task = asyncio.create_task(value)
                if cancel is not None:
                    while not task.done():
                        if cancel.is_set():
                            task.cancel()
                            try: await task
                            except asyncio.CancelledError: pass
                            return ToolResult(ok=False, error="tool cancelled", elapsed_ms=round((time.perf_counter()-started)*1000))
                        await asyncio.sleep(0.05)
                content = await task
            else:
                content = value
            return ToolResult(
                ok=True,
                content=content,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        except (KeyError, ValidationError, ValueError, OSError) as exc:
            return ToolResult(
                ok=False,
                error=str(exc),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )

from __future__ import annotations

import asyncio
import inspect
import time
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from ..isolation import IsolationPolicy, run_isolated
from ..types import Risk, ToolResult


def _network_error_text(exc: BaseException, tool: Any) -> str:
    """Short, URL-free description of a contained network or decoder failure."""
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}"
    elif isinstance(exc, RecursionError):
        detail = "response nesting too deep"
    else:
        detail = "network request failed"
    text = f"{type(exc).__name__}: {detail}"
    if getattr(tool, "risk", Risk.READ) != Risk.READ:
        text += "; outcome unknown, verify before retrying"
    return text


class Tool(ABC):
    name: str
    description: str
    risk: Risk = Risk.READ
    arguments_model: type[BaseModel]
    # Set to an IsolationPolicy to run this tool in a killable child process. Use it for
    # tools that can block inside synchronous or native code, where cooperative
    # cancellation cannot interrupt them. The tool, its arguments and result must pickle.
    isolation: IsolationPolicy | None = None

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

    async def aclose(self) -> None:
        async with AsyncExitStack() as cleanup:
            for tool in self._tools.values():
                close = getattr(tool, "aclose", None)
                if close is not None:
                    cleanup.push_async_callback(close)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any], cancel=None, *, owner_id: str) -> ToolResult:
        started = time.perf_counter()
        if cancel is not None and cancel.is_set():
            return ToolResult(ok=False, error="tool cancelled", elapsed_ms=0)
        try:
            tool = self.get(name)
            parsed = tool.arguments_model.model_validate(arguments)
            if tool.isolation is not None:
                outcome = await run_isolated(tool, parsed, tool.isolation, cancel, owner_id=owner_id)
                elapsed = round((time.perf_counter() - started) * 1000)
                if outcome.ok:
                    return ToolResult(ok=True, content=outcome.value, elapsed_ms=elapsed)
                return ToolResult(ok=False, error=outcome.error, elapsed_ms=elapsed)
            # Signature errors occur before invoking the action. An internal TypeError/
            # ValueError may follow a side effect and must never trigger a replay.
            parameters = inspect.signature(tool.run).parameters
            options = {}
            if "cancel" in parameters:
                options["cancel"] = cancel
            if "owner_id" in parameters:
                options["owner_id"] = owner_id
            if cancel is not None and cancel.is_set():
                return ToolResult(ok=False, error="tool cancelled", elapsed_ms=round((time.perf_counter()-started)*1000))
            value = tool.run(parsed, **options)
            if inspect.isawaitable(value):
                task = asyncio.create_task(value)
                try:
                    if cancel is not None:
                        while not task.done():
                            if cancel.is_set():
                                task.cancel()
                                try: await task
                                except asyncio.CancelledError: pass
                                return ToolResult(ok=False, error="tool cancelled", elapsed_ms=round((time.perf_counter()-started)*1000))
                            await asyncio.sleep(0.05)
                    content = await task
                except asyncio.CancelledError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
            else:
                content = value
            return ToolResult(
                ok=True,
                content=content,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        except (KeyError, TypeError, ValidationError, ValueError, OSError) as exc:
            return ToolResult(
                ok=False,
                error=str(exc),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
        except (httpx.HTTPError, RecursionError) as exc:
            # Network faults and decoder recursion are normal tool failures. The text names the
            # class and status only: str(HTTPStatusError) embeds the request URL and query.
            return ToolResult(
                ok=False,
                error=_network_error_text(exc, tool),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )


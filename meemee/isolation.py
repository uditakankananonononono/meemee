"""Process-isolated tool execution with forced interruption.

Cooperative cancellation (``asyncio`` task cancellation) cannot stop a tool that is
stuck inside blocking Python or third-party native code: the event loop never gets
control back. This module runs such tools in a dedicated child process. The parent
watches the cancellation event and a hard deadline; when either fires it sends
SIGTERM, waits a short grace period, then SIGKILL. The child is always reaped, so
no orphan keeps running after the tool call returns.

Only picklable tools, arguments and results can cross the process boundary. The
child uses the ``spawn`` start method so it never inherits the parent's threads,
locks or open database connections.
"""
from __future__ import annotations

import asyncio
import inspect
import multiprocessing as mp
import threading
import time
import traceback
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from pydantic import BaseModel

_MAX_TRACEBACK = 4000


@dataclass(frozen=True)
class IsolationPolicy:
    """How an isolated tool call is bounded."""

    timeout_s: float = 60.0
    grace_s: float = 2.0
    poll_s: float = 0.02

    def __post_init__(self) -> None:
        if not 0 < self.timeout_s <= 86_400:
            raise ValueError("timeout_s must be in (0, 86400]")
        if not 0 <= self.grace_s <= 60:
            raise ValueError("grace_s must be in [0, 60]")
        if not 0 < self.poll_s <= 1:
            raise ValueError("poll_s must be in (0, 1]")


@dataclass(frozen=True)
class IsolatedOutcome:
    """Result of one isolated call. ``status`` is ok, error, cancelled, timeout or crashed."""

    status: str
    value: Any = None
    error: str | None = None
    exit_code: int | None = None
    killed: bool = False
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _child_main(conn: Connection, tool: Any, arguments: BaseModel) -> None:
    try:
        value = tool.run(arguments)
        if inspect.isawaitable(value):
            value = asyncio.run(_await(value))
        conn.send(("ok", value))
    except BaseException as exc:  # noqa: BLE001 - report every failure, including SystemExit
        detail = f"{type(exc).__name__}: {exc}"
        try:
            conn.send(("error", detail, traceback.format_exc()[-_MAX_TRACEBACK:]))
        except Exception:  # noqa: BLE001, S110 - parent reports a crash if the pipe is gone
            pass
    finally:
        conn.close()


async def _await(value: Any) -> Any:
    return await value


def _stop(process: mp.process.BaseProcess, grace_s: float) -> bool:
    """Terminate then kill. Returns True when SIGKILL was needed."""
    if not process.is_alive():
        process.join(0)
        return False
    process.terminate()
    process.join(grace_s)
    if process.is_alive():
        process.kill()
        process.join(5)
        return True
    return False


def _cancel_requested(cancel: Any) -> bool:
    return cancel is not None and bool(cancel.is_set())


def run_isolated_sync(
    tool: Any,
    arguments: BaseModel,
    policy: IsolationPolicy | None = None,
    cancel: threading.Event | asyncio.Event | None = None,
) -> IsolatedOutcome:
    """Run ``tool.run(arguments)`` in a child process and block until it settles."""
    policy = policy or IsolationPolicy()
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child_main, args=(child_conn, tool, arguments), daemon=True)
    started = time.perf_counter()

    def elapsed() -> int:
        return round((time.perf_counter() - started) * 1000)

    try:
        process.start()
    except Exception as exc:  # noqa: BLE001 - unpicklable tool or arguments
        parent_conn.close()
        child_conn.close()
        return IsolatedOutcome("error", error=f"could not start isolated tool: {exc}", elapsed_ms=elapsed())
    child_conn.close()
    deadline = started + policy.timeout_s
    try:
        while True:
            if parent_conn.poll(policy.poll_s):
                try:
                    message = parent_conn.recv()
                except EOFError:
                    message = None
                process.join(policy.grace_s)
                killed = _stop(process, policy.grace_s)
                if message is None:
                    return IsolatedOutcome("crashed", error="tool process exited without a result",
                                           exit_code=process.exitcode, killed=killed, elapsed_ms=elapsed())
                if message[0] == "ok":
                    return IsolatedOutcome("ok", value=message[1], exit_code=process.exitcode,
                                           killed=killed, elapsed_ms=elapsed())
                return IsolatedOutcome("error", error=message[1], value={"traceback": message[2]},
                                       exit_code=process.exitcode, killed=killed, elapsed_ms=elapsed())
            if not process.is_alive():
                if parent_conn.poll(0):
                    continue
                process.join(0)
                return IsolatedOutcome("crashed", error=f"tool process died with exit code {process.exitcode}",
                                       exit_code=process.exitcode, elapsed_ms=elapsed())
            if _cancel_requested(cancel):
                killed = _stop(process, policy.grace_s)
                return IsolatedOutcome("cancelled", error="tool cancelled", exit_code=process.exitcode,
                                       killed=killed, elapsed_ms=elapsed())
            if time.perf_counter() >= deadline:
                killed = _stop(process, policy.grace_s)
                return IsolatedOutcome("timeout", error=f"tool exceeded {policy.timeout_s:g}s hard timeout",
                                       exit_code=process.exitcode, killed=killed, elapsed_ms=elapsed())
    finally:
        if process.is_alive():
            _stop(process, policy.grace_s)
        parent_conn.close()


async def run_isolated(
    tool: Any,
    arguments: BaseModel,
    policy: IsolationPolicy | None = None,
    cancel: threading.Event | asyncio.Event | None = None,
) -> IsolatedOutcome:
    """Async wrapper: supervises the child from a worker thread so the event loop stays free."""
    return await asyncio.to_thread(run_isolated_sync, tool, arguments, policy, cancel)

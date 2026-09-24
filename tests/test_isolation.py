from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest
from pydantic import BaseModel

from meemee.isolation import IsolationPolicy, run_isolated, run_isolated_sync
from meemee.tools.base import Tool, ToolRegistry


class Args(BaseModel):
    seconds: float = 0.0
    value: int = 0
    pid_file: str | None = None


class SleepingNativeTool(Tool):
    """Blocks in time.sleep, which asyncio cancellation cannot interrupt."""

    name = "test.block"
    description = "blocks the process"
    arguments_model = Args
    isolation = IsolationPolicy(timeout_s=30, grace_s=0.5)

    def run(self, arguments: Args):
        if arguments.pid_file:
            with open(arguments.pid_file, "w") as handle:
                handle.write(str(os.getpid()))
        time.sleep(arguments.seconds)
        return {"value": arguments.value * 2, "pid": os.getpid()}


class QuickTimeoutTool(SleepingNativeTool):
    name = "test.quick"
    isolation = IsolationPolicy(timeout_s=0.5, grace_s=0.2)


class AsyncTool(Tool):
    name = "test.async"
    description = "async tool"
    arguments_model = Args
    isolation = IsolationPolicy(timeout_s=30)

    async def run(self, arguments: Args):
        await asyncio.sleep(arguments.seconds)
        return arguments.value + 1


class FailingTool(Tool):
    name = "test.fail"
    description = "raises"
    arguments_model = Args
    isolation = IsolationPolicy(timeout_s=30)

    def run(self, arguments: Args):
        raise RuntimeError("boom")


class SignalIgnoringTool(Tool):
    name = "test.stubborn"
    description = "ignores SIGTERM"
    arguments_model = Args

    def run(self, arguments: Args):
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(arguments.seconds)
        return "unreachable"


class DyingTool(Tool):
    name = "test.die"
    description = "hard exit"
    arguments_model = Args

    def run(self, arguments: Args):
        os._exit(7)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid(path) -> int:
    for _ in range(500):
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(0.01)
    raise AssertionError("child never wrote its pid")


def test_result_crosses_process_boundary():
    outcome = run_isolated_sync(SleepingNativeTool(), Args(value=21))
    assert outcome.ok and outcome.value["value"] == 42
    assert outcome.value["pid"] != os.getpid()
    assert outcome.exit_code == 0 and not outcome.killed


def test_async_tool_runs_in_child():
    assert run_isolated_sync(AsyncTool(), Args(value=4, seconds=0.01)).value == 5


def test_tool_exception_is_reported_with_traceback():
    outcome = run_isolated_sync(FailingTool(), Args())
    assert outcome.status == "error"
    assert outcome.error == "RuntimeError: boom"
    assert "boom" in outcome.value["traceback"]


def test_hard_timeout_kills_blocked_child(tmp_path):
    pid_file = tmp_path / "pid"
    policy = IsolationPolicy(timeout_s=1.0, grace_s=0.5)
    started = time.perf_counter()
    outcome = run_isolated_sync(SleepingNativeTool(), Args(seconds=60, pid_file=str(pid_file)), policy)
    assert outcome.status == "timeout"
    assert time.perf_counter() - started < 10
    assert not _alive(_wait_for_pid(pid_file))


def test_cancellation_kills_blocked_child(tmp_path):
    pid_file = tmp_path / "pid"
    cancel = threading.Event()
    result = {}
    thread = threading.Thread(target=lambda: result.update(
        outcome=run_isolated_sync(SleepingNativeTool(), Args(seconds=60, pid_file=str(pid_file)),
                                  IsolationPolicy(timeout_s=60, grace_s=0.5), cancel)))
    thread.start()
    pid = _wait_for_pid(pid_file)
    cancel.set()
    thread.join(10)
    assert not thread.is_alive()
    assert result["outcome"].status == "cancelled"
    assert not _alive(pid)


def test_sigterm_ignoring_child_is_sigkilled():
    policy = IsolationPolicy(timeout_s=1.0, grace_s=0.3)
    outcome = run_isolated_sync(SignalIgnoringTool(), Args(seconds=60), policy)
    assert outcome.status == "timeout"
    assert outcome.killed is True
    assert outcome.exit_code is not None and outcome.exit_code < 0


def test_child_crash_is_reported():
    outcome = run_isolated_sync(DyingTool(), Args())
    assert outcome.status == "crashed"
    assert outcome.exit_code == 7


def test_unpicklable_tool_fails_cleanly():
    tool = SleepingNativeTool()
    tool.lock = threading.Lock()
    outcome = run_isolated_sync(tool, Args())
    assert outcome.status == "error"
    assert "could not start isolated tool" in outcome.error


def test_policy_validation():
    with pytest.raises(ValueError):
        IsolationPolicy(timeout_s=0)
    with pytest.raises(ValueError):
        IsolationPolicy(grace_s=-1)


async def test_registry_cancels_blocking_tool_and_loop_stays_responsive(tmp_path):
    registry = ToolRegistry()
    registry.register(SleepingNativeTool())
    pid_file = tmp_path / "pid"
    cancel = threading.Event()
    task = asyncio.create_task(registry.execute(
        "test.block", {"seconds": 60, "pid_file": str(pid_file)}, cancel=cancel))
    ticks = 0
    while not (pid_file.exists() and pid_file.read_text()):
        await asyncio.sleep(0.01)
        ticks += 1
    assert ticks > 0  # event loop kept running while the tool blocked
    cancel.set()
    result = await asyncio.wait_for(task, 10)
    assert result.ok is False and result.error == "tool cancelled"
    assert not _alive(int(pid_file.read_text()))


async def test_registry_isolated_success_and_timeout():
    registry = ToolRegistry()
    registry.register(SleepingNativeTool())
    ok = await registry.execute("test.block", {"value": 5})
    assert ok.ok and ok.content["value"] == 10
    registry.register(QuickTimeoutTool())
    timed_out = await registry.execute("test.quick", {"seconds": 30})
    assert timed_out.ok is False and "hard timeout" in timed_out.error


async def test_async_entrypoint():
    outcome = await run_isolated(AsyncTool(), Args(value=1))
    assert outcome.ok and outcome.value == 2

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class ShellArgs(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=30, ge=0.1, le=300)


async def _finish_cleanup(task: asyncio.Task):
    """Retain ownership through repeated cancellation until cleanup has finished."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class ShellCommand(Tool):
    """Execute an allowlisted program in an owned POSIX session.

    Cancellation/timeout stop the process group, not an arbitrary process tree.
    Descendants that deliberately leave the group/session are outside this boundary.
    Windows is unsupported: no process is launched there.
    """

    name = "workspace.run_command"
    description = "Run one allowlisted executable in the workspace; argv is passed directly."
    risk = Risk.EXECUTE
    arguments_model = ShellArgs
    terminate_grace_seconds = 0.25
    drain_grace_seconds = 1.0

    def __init__(self, root: Path, allowed: set[str]):
        self.root = root.resolve()
        self.allowed = allowed

    @staticmethod
    def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        # start_new_session makes the new child's PID its owned group ID. Do not
        # look up the caller's group, or stop at the leader's returncode: children
        # can still be alive after the leader exits.
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    async def _stop(self, process: asyncio.subprocess.Process, output: asyncio.Task) -> None:
        self._signal_group(process, signal.SIGTERM)
        await asyncio.sleep(self.terminate_grace_seconds)
        self._signal_group(process, signal.SIGKILL)
        # communicate owns both pipe readers and the leader's wait/reap.
        try:
            await asyncio.wait_for(asyncio.shield(output), self.drain_grace_seconds)
        except asyncio.TimeoutError:
            # An escaped descendant can retain inherited pipes. Close local
            # handles rather than waiting forever for a process we do not own.
            # asyncio exposes no public Process.close() API.
            process._transport.close()
            output.cancel()
            await asyncio.gather(output, return_exceptions=True)
        await process.wait()

    async def run(self, arguments: ShellArgs) -> dict[str, object]:
        executable = Path(arguments.argv[0]).name
        if executable not in self.allowed:
            raise ValueError(f"executable not allowlisted: {executable}")
        if os.name != "posix":
            raise ValueError(
                "ShellCommand process-group ownership requires POSIX; Windows unavailable"
            )
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home()), "LANG": "C.UTF-8"}
        # Shield spawn so cancellation cannot lose the newly-created process
        # before its handle reaches us. Recover that handle before cleanup.
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *arguments.argv,
                cwd=self.root,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        )
        process = None
        output = None
        try:
            process = await asyncio.shield(spawn)
            output = asyncio.create_task(process.communicate())
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(output), arguments.timeout_seconds
            )
        except asyncio.CancelledError:
            if process is None:
                # A spawn error has no process to stop; cancellation still wins.
                try:
                    process = await _finish_cleanup(spawn)
                except Exception:  # noqa: BLE001 - cancellation wins over any spawn failure
                    raise asyncio.CancelledError from None
            if output is None:
                output = asyncio.create_task(process.communicate())
            await _finish_cleanup(asyncio.create_task(self._stop(process, output)))
            raise
        except asyncio.TimeoutError:
            await _finish_cleanup(asyncio.create_task(self._stop(process, output)))
            raise ValueError(f"command timed out after {arguments.timeout_seconds}s") from None
        cap = 200_000
        return {
            "exit_code": process.returncode,
            "stdout": stdout[:cap].decode("utf-8", errors="replace"),
            "stderr": stderr[:cap].decode("utf-8", errors="replace"),
            "truncated": len(stdout) > cap or len(stderr) > cap,
        }

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class ShellArgs(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=30, ge=0.1, le=300)


class ShellCommand(Tool):
    """Execute one allowlisted program without a shell or string interpolation."""

    name = "workspace.run_command"
    description = "Run one allowlisted executable in the workspace; argv is passed directly."
    risk = Risk.EXECUTE
    arguments_model = ShellArgs

    def __init__(self, root: Path, allowed: set[str]):
        self.root = root.resolve()
        self.allowed = allowed
        # Bind names to operator-selected PATH executables when configured.
        # A caller cannot substitute a same-basename workspace binary.
        self.executables = {}
        for name in allowed:
            candidate = shutil.which(name)
            if candidate:
                self.executables[name] = Path(candidate).resolve()

    async def run(self, arguments: ShellArgs) -> dict[str, object]:
        requested = arguments.argv[0]
        name = Path(requested).name
        expected = self.executables.get(requested) or self.executables.get(name)
        candidate = shutil.which(requested)
        if expected is None or candidate is None or Path(candidate).resolve() != expected:
            raise ValueError(f"executable not allowlisted: {requested}")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home()), "LANG": "C.UTF-8"}
        process = await asyncio.create_subprocess_exec(
            str(expected), *arguments.argv[1:],
            cwd=self.root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        cap = 200_000

        async def capture(stream):
            retained = bytearray()
            truncated = False
            while chunk := await stream.read(65536):
                room = cap - len(retained)
                retained.extend(chunk[:room])
                truncated = truncated or len(chunk) > room
            return retained.decode("utf-8", errors="replace"), truncated

        tasks = [asyncio.create_task(capture(process.stdout)),
                 asyncio.create_task(capture(process.stderr)),
                 asyncio.create_task(process.wait())]

        async def stop():
            # Descendants may keep pipes open after their parent exits. Kill
            # the POSIX process group, then reap and cancel pending readers.
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await process.wait()

        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(*tasks), arguments.timeout_seconds)
        except asyncio.CancelledError:
            await stop()
            raise
        except asyncio.TimeoutError:
            await stop()
            raise ValueError(f"command timed out after {arguments.timeout_seconds}s") from None
        except BaseException:
            await stop()
            raise
        return {
            "exit_code": process.returncode,
            "stdout": stdout[0],
            "stderr": stderr[0],
            "truncated": stdout[1] or stderr[1],
        }

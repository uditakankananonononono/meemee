from __future__ import annotations

import asyncio
import os
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

    async def run(self, arguments: ShellArgs) -> dict[str, object]:
        executable = Path(arguments.argv[0]).name
        if executable not in self.allowed:
            raise ValueError(f"executable not allowlisted: {executable}")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home()), "LANG": "C.UTF-8"}
        process = await asyncio.create_subprocess_exec(
            *arguments.argv,
            cwd=self.root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), arguments.timeout_seconds)
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError(f"command timed out after {arguments.timeout_seconds}s") from None
        cap = 200_000
        return {
            "exit_code": process.returncode,
            "stdout": stdout[:cap].decode("utf-8", errors="replace"),
            "stderr": stderr[:cap].decode("utf-8", errors="replace"),
            "truncated": len(stdout) > cap or len(stderr) > cap,
        }

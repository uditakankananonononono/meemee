from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from pydantic import BaseModel, Field, StrictInt

from ..types import Risk
from .base import Tool


class GitReadArgs(BaseModel):
    operation: str = Field(pattern="^(status|diff|log)$")
    limit: int = Field(default=20, ge=1, le=100)
    max_output_bytes: StrictInt = Field(default=200_000, ge=1, le=1_000_000)
    timeout_seconds: float = Field(default=30, ge=0.1, le=300)


class GitCommitArgs(BaseModel):
    message: str = Field(min_length=3, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=100)


class GitBase(Tool):
    def __init__(self, root: Path):
        self.root = root.resolve()

    async def git(self, *argv: str, cap=200_000, timeout=30) -> dict[str, object]:
        process = await asyncio.create_subprocess_exec(
            "git", *argv, cwd=self.root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        async def capture(stream):
            retained=bytearray();truncated=False
            while chunk := await stream.read(65536):
                room=cap-len(retained);retained.extend(chunk[:room])
                truncated=truncated or len(chunk)>room
            return retained.decode(errors="replace"),truncated
        tasks=[asyncio.create_task(capture(process.stdout)),asyncio.create_task(capture(process.stderr)),asyncio.create_task(process.wait())]
        async def stop():
            try:
                if os.name=="posix":os.killpg(process.pid,signal.SIGKILL)
                elif process.returncode is None:process.kill()
            except ProcessLookupError:pass
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await process.wait()
        try:stdout,stderr,_=await asyncio.wait_for(asyncio.gather(*tasks),timeout)
        except asyncio.TimeoutError:
            await stop();raise ValueError("git command timed out; write outcome unknown") from None
        except BaseException:
            await stop();raise
        result={"exit_code":process.returncode,"stdout":stdout[0],"stderr":stderr[0],"truncated":stdout[1] or stderr[1]}
        if process.returncode:
            raise ValueError(f"git {' '.join(argv)} failed: {result['stderr']}")
        return result


class GitInspect(GitBase):
    name = "git.inspect"
    description = "Inspect repository status, diff, or recent log."
    arguments_model = GitReadArgs

    async def run(self, arguments: GitReadArgs) -> dict[str, object]:
        # Read effective config keys, never filter command values. Refuse an
        # incomplete list rather than leaving undiscovered filters enabled.
        config = await self.git("config", "--null", "--name-only", "--list",
                                cap=1_000_000, timeout=arguments.timeout_seconds)
        if config["truncated"]:
            raise ValueError("git inspection filter config exceeds discovery limit")
        filters = set()
        for key in str(config["stdout"]).split("\0"):
            if key.startswith("filter.") and "." in key[len("filter."):]:
                name, setting = key[len("filter."):].rsplit(".", 1)
                if setting in {"clean", "smudge", "process", "required"}:
                    filters.add(name)
        overrides = ["-c", "core.fsmonitor=false"]
        for name in sorted(filters):
            for setting in ("clean", "smudge", "process"):
                overrides.extend(["-c", f"filter.{name}.{setting}="])
            overrides.extend(["-c", f"filter.{name}.required=false"])
        if arguments.operation == "status":
            command = ("status", "--short", "--branch")
        elif arguments.operation == "diff":
            command = ("diff", "--no-ext-diff", "--no-textconv", "--")
        else:
            command = ("log", f"-{arguments.limit}", "--oneline", "--decorate")
        return await self.git(*overrides, *command, cap=arguments.max_output_bytes,
                              timeout=arguments.timeout_seconds)


class GitCommit(GitBase):
    name = "git.commit"
    description = "Stage named workspace paths and create a local Git commit. Never pushes."
    risk = Risk.WRITE
    arguments_model = GitCommitArgs

    def safe_path(self, raw: str) -> str:
        resolved = (self.root / raw).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path escapes repository: {raw}")
        return str(resolved.relative_to(self.root))

    async def run(self, arguments: GitCommitArgs) -> dict[str, object]:
        paths = [":(literal)" + self.safe_path(path) for path in arguments.paths]
        await self.git("add", "--", *paths)
        result = await self.git("commit", "-m", arguments.message, "--", *paths)
        head = await self.git("rev-parse", "HEAD")
        return {"commit": str(head["stdout"]).strip(), "output": result["stdout"]}

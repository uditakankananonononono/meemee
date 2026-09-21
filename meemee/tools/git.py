from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class GitReadArgs(BaseModel):
    operation: str = Field(pattern="^(status|diff|log)$")
    limit: int = Field(default=20, ge=1, le=100)


class GitCommitArgs(BaseModel):
    message: str = Field(min_length=3, max_length=200)
    paths: list[str] = Field(min_length=1, max_length=100)


class GitBase(Tool):
    def __init__(self, root: Path):
        self.root = root.resolve()

    async def git(self, *argv: str) -> dict[str, object]:
        process = await asyncio.create_subprocess_exec(
            "git", *argv, cwd=self.root,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        result = {"exit_code": process.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}
        if process.returncode:
            raise ValueError(f"git {' '.join(argv)} failed: {result['stderr']}")
        return result


class GitInspect(GitBase):
    name = "git.inspect"
    description = "Inspect repository status, diff, or recent log."
    arguments_model = GitReadArgs

    async def run(self, arguments: GitReadArgs) -> dict[str, object]:
        if arguments.operation == "status":
            return await self.git("status", "--short", "--branch")
        if arguments.operation == "diff":
            return await self.git("diff", "--no-ext-diff", "--")
        return await self.git("log", f"-{arguments.limit}", "--oneline", "--decorate")


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
        paths = [self.safe_path(path) for path in arguments.paths]
        await self.git("add", "--", *paths)
        result = await self.git("commit", "-m", arguments.message, "--", *paths)
        head = await self.git("rev-parse", "HEAD")
        return {"commit": str(head["stdout"]).strip(), "output": result["stdout"]}

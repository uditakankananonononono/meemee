from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class PathArgs(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class WriteArgs(PathArgs):
    content: str = Field(max_length=2_000_000)


class WorkspaceTool(Tool):
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, raw: str) -> Path:
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("path escapes workspace")
        return path


class ReadFile(WorkspaceTool):
    name = "workspace.read_file"
    description = "Read a UTF-8 text file inside the configured workspace."
    arguments_model = PathArgs

    async def run(self, arguments: PathArgs) -> dict[str, str]:
        path = self.resolve(arguments.path)
        return {"path": str(path.relative_to(self.root)), "content": path.read_text(encoding="utf-8")}


class WriteFile(WorkspaceTool):
    name = "workspace.write_file"
    description = "Write a UTF-8 file inside the workspace, creating parent directories."
    risk = Risk.WRITE
    arguments_model = WriteArgs

    async def run(self, arguments: WriteArgs) -> dict[str, object]:
        path = self.resolve(arguments.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        return {"path": str(path.relative_to(self.root)), "bytes": len(arguments.content.encode())}

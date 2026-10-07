from __future__ import annotations

import os
import stat
import tempfile
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
        self._publish(path, arguments.content)
        return {"path": str(path.relative_to(self.root)), "bytes": len(arguments.content.encode())}

    @staticmethod
    def _publish(path: Path, content: str) -> None:
        # Publish only a complete same-filesystem write. A disk/encoding error
        # must leave the old destination intact, not a truncated user file.
        old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        fd, temporary_name = tempfile.mkstemp(prefix=".meemee-write-", dir=path.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(content, encoding="utf-8")
            if old_mode is not None:
                temporary.chmod(old_mode)
            with temporary.open("rb") as saved:
                os.fsync(saved.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

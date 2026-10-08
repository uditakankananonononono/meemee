"""Bounded raw-byte paging without claiming UTF-8 character alignment."""
import base64
import os
import stat

from pydantic import Field, StrictInt

from .filesystem import PathArgs, WorkspaceTool


class ChunkArgs(PathArgs):
    offset: StrictInt = Field(default=0, ge=0, le=9_223_372_036_854_775_000)
    max_bytes: StrictInt = Field(default=65536, ge=1, le=262144)


class ReadChunk(WorkspaceTool):
    name = "workspace.read_chunk"
    description = "Read one bounded binary-safe workspace file chunk by byte offset, encoded as base64."
    arguments_model = ChunkArgs

    async def run(self, arguments: ChunkArgs):
        path = self.resolve(arguments.path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("regular workspace file required")
            stream.seek(arguments.offset)
            raw = stream.read(arguments.max_bytes + 1)
        data = raw[:arguments.max_bytes]
        return {"path": str(path.relative_to(self.root)), "offset": arguments.offset,
                "base64": base64.b64encode(data).decode("ascii"), "bytes": len(data),
                "next_offset": arguments.offset + len(data), "eof": len(raw) <= arguments.max_bytes}

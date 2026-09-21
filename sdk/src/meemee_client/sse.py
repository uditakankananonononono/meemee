"""Incremental Server-Sent Events parser (WHATWG SSE semantics).

The Meemee job stream sends frames shaped as::

    id: <sequence>\nevent: <kind>\ndata: <json>\n\n

plus heartbeat comment lines (``: heartbeat <cursor>``) and terminates with an
``event: error`` frame if the job disappears mid-stream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SSEMessage:
    """One parsed unit: an event frame or a comment (heartbeat) line."""

    kind: Literal["event", "comment"]
    #: Event type for kind="event" (defaults to "message" per spec); None for comments.
    event: str | None = None
    #: Joined data lines for kind="event"; None for comments.
    data: str | None = None
    #: Last-Event-ID carried by the frame; None when the frame has no id field.
    id: str | None = None
    #: Comment text (without the leading colon) for kind="comment".
    comment: str | None = None


class SSEParser:
    """Feed byte chunks, get back complete messages. Safe to split anywhere."""

    def __init__(self) -> None:
        self._buffer = b""
        self._data_lines: list[str] = []
        self._event_type = ""
        self._last_event_id: str | None = None

    @property
    def last_event_id(self) -> str | None:
        return self._last_event_id

    def feed(self, chunk: bytes) -> list[SSEMessage]:
        self._buffer += chunk
        messages: list[SSEMessage] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline == -1:
                break
            raw = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1:]
            line = raw.decode("utf-8", errors="replace")
            if line.endswith("\r"):
                line = line[:-1]
            message = self._process_line(line)
            if message is not None:
                messages.append(message)
        return messages

    def _process_line(self, line: str) -> SSEMessage | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return SSEMessage(kind="comment", comment=line[1:].removeprefix(" "))
        field, separator, value = line.partition(":")
        if not separator:
            # Spec: a line with no colon is treated as a field name with empty value.
            field, value = line, ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data_lines.append(value)
        elif field == "event":
            self._event_type = value
        elif field == "id":
            if "\x00" not in value:
                self._last_event_id = value
        # Unknown fields (including "retry", which the server never sends) are ignored.
        return None

    def _dispatch(self) -> SSEMessage | None:
        if not self._data_lines:
            # Spec: an event with no data is not dispatched; buffers still reset.
            self._event_type = ""
            return None
        data = "\n".join(self._data_lines)
        message = SSEMessage(
            kind="event",
            event=self._event_type or "message",
            data=data,
            id=self._last_event_id,
        )
        self._data_lines = []
        self._event_type = ""
        return message

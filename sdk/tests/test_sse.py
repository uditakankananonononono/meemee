"""SSEParser unit tests: WHATWG semantics against Meemee's frame shapes."""
from __future__ import annotations

from meemee_client.sse import SSEMessage, SSEParser


def feed_all(parser: SSEParser, body: bytes, chunk_size: int | None = None) -> list[SSEMessage]:
    messages: list[SSEMessage] = []
    if chunk_size is None:
        return parser.feed(body)
    for index in range(0, len(body), chunk_size):
        messages.extend(parser.feed(body[index:index + chunk_size]))
    return messages


def test_single_event_frame() -> None:
    parser = SSEParser()
    messages = parser.feed(b'id: 12\nevent: running\ndata: {"a":1}\n\n')
    assert len(messages) == 1
    message = messages[0]
    assert message.kind == "event"
    assert message.event == "running"
    assert message.data == '{"a":1}'
    assert message.id == "12"
    assert parser.last_event_id == "12"


def test_heartbeat_comment() -> None:
    parser = SSEParser()
    messages = parser.feed(b": heartbeat 7\n\n")
    assert messages == [SSEMessage(kind="comment", comment="heartbeat 7")]


def test_multi_line_data_is_joined() -> None:
    parser = SSEParser()
    messages = parser.feed(b"data: line one\ndata: line two\n\n")
    assert messages[0].data == "line one\nline two"


def test_default_event_type_is_message() -> None:
    parser = SSEParser()
    messages = parser.feed(b"data: hi\n\n")
    assert messages[0].event == "message"


def test_event_without_data_is_not_dispatched_and_type_resets() -> None:
    parser = SSEParser()
    assert parser.feed(b"event: queued\n\n") == []
    messages = parser.feed(b"data: x\n\n")
    assert messages[0].event == "message"


def test_id_persists_across_frames_until_replaced() -> None:
    parser = SSEParser()
    parser.feed(b"id: 5\ndata: a\n\n")
    messages = parser.feed(b"data: b\n\n")
    assert messages[0].id == "5"


def test_id_with_nul_is_ignored() -> None:
    parser = SSEParser()
    parser.feed(b"id: 5\ndata: a\n\n")
    parser.feed(b"id: 6\x00evil\ndata: b\n\n")
    assert parser.last_event_id == "5"


def test_chunked_delivery_split_mid_line_and_mid_frame() -> None:
    parser = SSEParser()
    body = b"id: 1\nevent: queued\ndata: {\"run_at\":\"soon\"}\n\n: heartbeat 1\n\nid: 2\nevent: done\ndata: {\"result\":{}}\n\n"
    messages = feed_all(parser, body, chunk_size=3)
    kinds = [(m.kind, m.event) for m in messages]
    assert kinds == [("event", "queued"), ("comment", None), ("event", "done")]


def test_crlf_line_endings() -> None:
    parser = SSEParser()
    messages = parser.feed(b"id: 3\r\nevent: failed\r\ndata: {\"error\":\"boom\"}\r\n\r\n")
    assert messages[0].data == '{"error":"boom"}'


def test_unknown_fields_are_ignored() -> None:
    parser = SSEParser()
    messages = parser.feed(b"retry: 3000\nfoo: bar\ndata: x\n\n")
    assert len(messages) == 1
    assert messages[0].data == "x"


def test_line_without_colon_is_field_with_empty_value() -> None:
    parser = SSEParser()
    messages = parser.feed(b"data\ndata: y\n\n")
    assert messages[0].data == "\ny"

"""SSE job streaming: live events, resume after drops, terminal handling."""
from __future__ import annotations

import httpx
import pytest
from conftest import JOB_ID, FlakyStream, make_client, sse_frame, sse_heartbeat

from meemee_client import NetworkError, NotFoundError, StreamError


def stream_response(*chunks: bytes) -> httpx.Response:
    body = b"".join(chunks)
    return httpx.Response(200, content=body, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


def test_stream_events_yields_until_terminal_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/jobs/{JOB_ID}/stream"
        assert request.headers["Accept"] == "text/event-stream"
        return stream_response(
            sse_frame(1, "queued", {"run_at": "now"}),
            sse_frame(2, "running", {"attempt": 1}),
            sse_frame(3, "done", {"result": {"summary": "ok"}}),
        )

    events = list(make_client(handler).jobs.stream_events(JOB_ID))
    assert [e.kind for e in events] == ["queued", "running", "done"]
    assert events[2].payload["result"]["summary"] == "ok"
    assert events[2].sequence == 3


def test_stream_events_passes_after_and_last_event_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "5"
        assert request.headers["Last-Event-ID"] == "5"
        return stream_response(sse_frame(6, "done"))

    events = list(make_client(handler).jobs.stream_events(JOB_ID, after=5))
    assert [e.sequence for e in events] == [6]


def test_stream_events_closes_client_side_on_cancelled() -> None:
    # The server leaves the stream open for cancelled jobs (heartbeats only);
    # the SDK must close on the cancelled event itself.
    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(
            sse_frame(1, "running"),
            sse_frame(2, "cancel_requested"),
            sse_frame(3, "cancelled"),
            sse_heartbeat(3),
            sse_heartbeat(3),
        )

    events = list(make_client(handler).jobs.stream_events(JOB_ID))
    assert [e.kind for e in events] == ["running", "cancel_requested", "cancelled"]


def test_stream_events_heartbeats_go_to_callback() -> None:
    beats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(
            sse_frame(1, "running"),
            sse_heartbeat(1),
            sse_heartbeat(1),
            sse_frame(2, "done"),
        )

    events = list(make_client(handler).jobs.stream_events(JOB_ID, on_heartbeat=beats.append))
    assert beats == ["heartbeat 1", "heartbeat 1"]
    assert [e.kind for e in events] == ["running", "done"]


def test_stream_events_resumes_with_last_event_id_after_drop(sleeper) -> None:
    seen_headers: list[str | None] = []
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        seen_headers.append(request.headers.get("Last-Event-ID"))
        if len(attempts) == 1:
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  stream=FlakyStream(
                                      [sse_frame(1, "queued"), sse_frame(2, "running")],
                                      httpx.RemoteProtocolError("peer closed", request=request),
                                  ))
        return stream_response(sse_frame(3, "done", {"result": {}}))

    events = list(make_client(handler, sleeper=sleeper).jobs.stream_events(JOB_ID))
    assert [e.kind for e in events] == ["queued", "running", "done"]
    assert seen_headers == [None, "2"]  # resumed exactly after the last delivered event


def test_stream_events_reconnect_budget_exhausted_raises_network_error(sleeper) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=FlakyStream(
                                  [sse_frame(1, "running")],
                                  httpx.ReadError("dropped", request=request),
                              ))

    with pytest.raises(NetworkError, match="last delivered event id 1"):
        list(make_client(handler, sleeper=sleeper).jobs.stream_events(JOB_ID, max_reconnects=2))


def test_stream_events_server_error_frame_raises_stream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(
            sse_frame(1, "running"),
            b'event: error\ndata: {"detail":"job not found"}\n\n',
        )

    with pytest.raises(StreamError, match="job not found"):
        list(make_client(handler).jobs.stream_events(JOB_ID))


def test_stream_events_malformed_frame_raises_stream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(b"id: 1\nevent: queued\ndata: not-json\n\n")

    with pytest.raises(StreamError, match="malformed"):
        list(make_client(handler).jobs.stream_events(JOB_ID))


def test_stream_events_unknown_job_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "job not found"})

    with pytest.raises(NotFoundError):
        list(make_client(handler).jobs.stream_events("missing"))


def test_stream_events_no_reconnect_when_disabled(sleeper) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=FlakyStream([sse_frame(1, "running")],
                                                 httpx.ReadError("dropped", request=request)))

    with pytest.raises(NetworkError):
        list(make_client(handler, sleeper=sleeper).jobs.stream_events(JOB_ID, reconnect=False))


def test_stream_events_clean_eof_after_terminal_returns() -> None:
    # Server closes the stream itself after done/failed; even if the terminal
    # frame parser somehow missed it, a clean EOF ends iteration quietly.
    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(sse_frame(1, "failed", {"error": "model down"}))

    events = list(make_client(handler).jobs.stream_events(JOB_ID))
    assert [e.kind for e in events] == ["failed"]

"""WebSocket job streaming (sync and asyncio) against a scripted local WebSocket server.

The server is a real ``websockets`` server on a loopback port, so the SDK's
handshake, headers, close-code handling and reconnects run over real sockets.
Each test registers a script: a list of connection behaviours consumed in order.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Callable
from http import HTTPStatus

import pytest
from conftest import JOB_ID, event_dict

pytest.importorskip("websockets")

from meemee_client import (
    AsyncMeemeeClient,
    AuthenticationError,
    BadRequestError,
    MeemeeClient,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    StreamError,
)
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response


def frame(sequence: int, kind: str) -> str:
    return json.dumps(event_dict(sequence, kind))


class ScriptedServer:
    """Loopback WebSocket server; each connection runs the next scripted step."""

    def __init__(self) -> None:
        self.steps: list[Callable] = []
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.loop = asyncio.new_event_loop()
        self.port = 0
        ready = threading.Event()

        async def handler(connection) -> None:
            step = self.steps.pop(0) if self.steps else None
            if step is None:
                await connection.close(1011, "no script")
                return
            await step(connection)

        def reject(connection, request):
            self.requests.append((request.path, dict(request.headers.raw_items())))
            status = getattr(self, "http_status", None)
            if status is not None:
                self.http_status = None
                return Response(status, HTTPStatus(status).phrase, Headers(), b"")
            return None

        async def main() -> None:
            self.server = await serve(handler, "127.0.0.1", 0, process_request=reject)
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()
            try:
                await self.server.serve_forever()
            except asyncio.CancelledError:
                pass
            await self.server.wait_closed()

        self.thread = threading.Thread(target=lambda: self.loop.run_until_complete(main()), daemon=True)
        self.thread.start()
        ready.wait(5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.server.close)
        self.thread.join(5)


def send_then(*frames: str, close: tuple[int, str] | None = (1000, ""), abort: bool = False):
    async def step(connection) -> None:
        for item in frames:
            await connection.send(item)
        if abort:
            connection.transport.abort()  # drop the TCP connection with no close frame
            return
        if close is not None:
            await connection.close(*close)
        else:
            await connection.wait_closed()
    return step


class NoSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class AsyncNoSleep(NoSleep):
    async def __call__(self, seconds: float) -> None:  # type: ignore[override]
        self.calls.append(seconds)


@pytest.fixture()
def server():
    srv = ScriptedServer()
    yield srv
    srv.stop()


def sync_client(server: ScriptedServer, sleeper: NoSleep | None = None) -> MeemeeClient:
    return MeemeeClient(server.base, auth="mee_ws", sleeper=sleeper or NoSleep())


def async_client(server: ScriptedServer, sleeper: AsyncNoSleep | None = None) -> AsyncMeemeeClient:
    return AsyncMeemeeClient(server.base, auth="mee_ws", sleeper=sleeper or AsyncNoSleep())


# ------------------------------------------------------------------ sync


def test_sync_streams_to_terminal_with_auth_header_and_cursor(server) -> None:
    server.steps.append(send_then(frame(1, "queued"), frame(2, "running"), frame(3, "done"), close=None))
    events = list(sync_client(server).jobs.stream_ws(JOB_ID))
    assert [e.kind for e in events] == ["queued", "running", "done"]
    path, headers = server.requests[0]
    assert path == f"/v1/jobs/{JOB_ID}/ws?after=0"
    assert headers["Authorization"] == "Bearer mee_ws"
    assert headers["User-Agent"].startswith("meemee-client/")


def test_sync_resumes_after_abrupt_drop_without_duplicates(server) -> None:
    sleeper = NoSleep()
    server.steps.append(send_then(frame(1, "queued"), frame(2, "running"), abort=True))
    # The resumed connection replays event 2 (e.g. a racing server); it is skipped.
    server.steps.append(send_then(frame(2, "running"), frame(3, "done")))
    events = list(sync_client(server, sleeper).jobs.stream_ws(JOB_ID))
    assert [e.sequence for e in events] == [1, 2, 3]
    assert [p for p, _ in server.requests] == [f"/v1/jobs/{JOB_ID}/ws?after=0", f"/v1/jobs/{JOB_ID}/ws?after=2"]
    assert sleeper.calls == [1.0]


def test_sync_resumes_after_server_error_close(server) -> None:
    server.steps.append(send_then(frame(1, "queued"), close=(1011, "internal error")))
    server.steps.append(send_then(frame(2, "failed")))
    assert [e.kind for e in sync_client(server).jobs.stream_ws(JOB_ID, after=0)] == ["queued", "failed"]
    assert server.requests[1][0].endswith("after=1")


def test_sync_explicit_after_and_normal_close_without_terminal(server) -> None:
    server.steps.append(send_then(frame(5, "running")))
    events = list(sync_client(server).jobs.stream_ws(JOB_ID, after=4))
    assert [e.sequence for e in events] == [5]
    assert server.requests[0][0].endswith("after=4")


@pytest.mark.parametrize(("code", "error"), [
    (4401, AuthenticationError), (4403, PermissionDeniedError), (4404, NotFoundError), (4400, BadRequestError),
])
def test_sync_rejection_codes_raise_typed_errors_without_reconnect(server, code, error) -> None:
    server.steps.append(send_then(close=(code, "rejected")))
    with pytest.raises(error) as caught:
        list(sync_client(server).jobs.stream_ws(JOB_ID))
    assert len(server.requests) == 1
    if code == 4403:
        assert caught.value.missing_scope == "jobs:read"


@pytest.mark.parametrize(("status", "error"), [(403, PermissionDeniedError), (401, AuthenticationError), (404, NotFoundError)])
def test_sync_http_handshake_rejection(server, status, error) -> None:
    server.http_status = status
    with pytest.raises(error):
        list(sync_client(server).jobs.stream_ws(JOB_ID))
    assert len(server.requests) == 1


def test_sync_http_503_handshake_is_retried(server) -> None:
    server.http_status = 503
    server.steps.append(send_then(frame(1, "done")))
    assert [e.kind for e in sync_client(server).jobs.stream_ws(JOB_ID)] == ["done"]
    assert len(server.requests) == 2


def test_sync_malformed_frame_raises_stream_error(server) -> None:
    server.steps.append(send_then("not-json", close=None))
    with pytest.raises(StreamError, match="malformed"):
        list(sync_client(server).jobs.stream_ws(JOB_ID))
    server.steps.append(send_then(json.dumps({"sequence": "x"}), close=None))
    with pytest.raises(StreamError, match="unparseable"):
        list(sync_client(server).jobs.stream_ws(JOB_ID))


def test_sync_budget_exhausted_and_reconnect_disabled(server) -> None:
    for _ in range(3):
        server.steps.append(send_then(frame(1, "running"), abort=True))
    sleeper = NoSleep()
    with pytest.raises(NetworkError, match="last delivered event id 1"):
        list(sync_client(server, sleeper).jobs.stream_ws(JOB_ID, max_reconnects=2))
    assert sleeper.calls == [1.0, 2.0]
    server.steps.clear()
    server.steps.append(send_then(frame(1, "running"), abort=True))
    with pytest.raises(NetworkError):
        list(sync_client(server).jobs.stream_ws(JOB_ID, reconnect=False))


def test_sync_connection_refused_counts_against_budget() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    sleeper = NoSleep()
    client = MeemeeClient(f"http://127.0.0.1:{port}", auth="t", sleeper=sleeper)
    with pytest.raises(NetworkError, match="reconnect budget"):
        list(client.jobs.stream_ws(JOB_ID, max_reconnects=1, open_timeout=2))
    assert sleeper.calls == [1.0]


def test_sync_consumer_break_closes_the_socket(server) -> None:
    closed = threading.Event()

    async def step(connection) -> None:
        await connection.send(frame(1, "queued"))
        await connection.wait_closed()
        closed.set()

    server.steps.append(step)
    stream = sync_client(server).jobs.stream_ws(JOB_ID)
    assert next(stream).kind == "queued"
    stream.close()
    assert closed.wait(5)


def test_https_base_url_maps_to_wss() -> None:
    from meemee_client._ws import ws_url
    assert ws_url("https://api.example.com/", "j1", 3) == "wss://api.example.com/v1/jobs/j1/ws?after=3"
    assert ws_url("http://h:1", "j1", -5) == "ws://h:1/v1/jobs/j1/ws?after=0"


# ----------------------------------------------------------------- async


async def test_async_streams_resumes_and_raises_like_sync(server) -> None:
    sleeper = AsyncNoSleep()
    server.steps.append(send_then(frame(1, "queued"), abort=True))
    server.steps.append(send_then(frame(1, "queued"), frame(2, "cancelled"), close=None))
    client = async_client(server, sleeper)
    events = [e async for e in client.jobs.stream_ws(JOB_ID)]
    assert [e.kind for e in events] == ["queued", "cancelled"]
    assert server.requests[1][0].endswith("after=1")
    assert server.requests[0][1]["Authorization"] == "Bearer mee_ws"
    assert sleeper.calls == [1.0]

    server.steps.append(send_then(close=(4404, "job not found")))
    with pytest.raises(NotFoundError):
        [e async for e in client.jobs.stream_ws("missing")]
    server.http_status = 403
    with pytest.raises(PermissionDeniedError):
        [e async for e in client.jobs.stream_ws(JOB_ID)]
    server.steps.append(send_then("{broken", close=None))
    with pytest.raises(StreamError):
        [e async for e in client.jobs.stream_ws(JOB_ID)]
    for _ in range(2):
        server.steps.append(send_then(frame(1, "running"), abort=True))
    with pytest.raises(NetworkError, match="last delivered event id 1"):
        [e async for e in client.jobs.stream_ws(JOB_ID, max_reconnects=1)]
    await client.aclose()


async def test_async_blocking_auth_provider_used_for_handshake(server) -> None:
    class Provider:
        def authorization_header(self) -> str:
            return "Bearer from-thread"

    server.steps.append(send_then(frame(1, "done")))
    client = AsyncMeemeeClient(server.base, auth=Provider(), sleeper=AsyncNoSleep())
    assert [e.kind async for e in client.jobs.stream_ws(JOB_ID)] == ["done"]
    assert server.requests[0][1]["Authorization"] == "Bearer from-thread"

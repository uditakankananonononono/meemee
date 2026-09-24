"""AsyncMeemeeClient against a mocked transport speaking the server contract."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import BASE_URL, JOB_ID, event_dict, job_payload, sse_frame, sse_heartbeat
from meemee_client import (
    AsyncMeemeeClient,
    AuthenticationError,
    ConflictError,
    IdempotencyConflictError,
    JobStatus,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RetryPolicy,
    ServerError,
    StreamError,
    ValidationError,
    WaitTimeoutError,
)

NOW = "2026-09-21T12:00:00+00:00"


class AsyncSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make(handler: Callable[[httpx.Request], httpx.Response], *, auth: Any = "mee_testtoken",
         sleeper: AsyncSleeper | None = None, retry: RetryPolicy | None = None) -> AsyncMeemeeClient:
    return AsyncMeemeeClient(BASE_URL, auth=auth, transport=httpx.MockTransport(handler),
                             sleeper=sleeper or AsyncSleeper(), retry=retry)


class AsyncFlakyStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], exc: Exception | None) -> None:
        self._chunks, self._exc = chunks, exc

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        if self._exc is not None:
            raise self._exc

    async def aclose(self) -> None:
        pass


def sse(*frames: bytes, exc: Exception | None = None) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=AsyncFlakyStream(list(frames), exc))


async def test_health_ready_and_context_manager() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.117.0"})
        return httpx.Response(503, json={"status": "not_ready", "checks": {"db": "down"}})

    async with make(handler) as client:
        assert (await client.health()).version == "0.117.0"
        assert (await client.ready()).status == "not_ready"
    assert client._http.is_closed


async def test_bearer_header_user_agent_and_request_metadata() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=job_payload(), headers={
            "X-Request-ID": "req-1", "RateLimit-Limit": "60", "RateLimit-Remaining": "59", "RateLimit-Reset": "30"})

    client = make(handler)
    job = await client.jobs.get(JOB_ID)
    assert job.status is JobStatus.QUEUED
    assert seen[0].headers["Authorization"] == "Bearer mee_testtoken"
    assert seen[0].headers["User-Agent"].startswith("meemee-client/")
    assert client.last_response_info.request_id == "req-1"
    assert client.last_response_info.rate_limit.remaining == 59


async def test_native_async_auth_provider_is_awaited() -> None:
    class AsyncProvider:
        calls = 0

        def authorization_header(self) -> str:  # pragma: no cover - must not be used
            raise AssertionError("sync path used")

        async def authorization_header_async(self) -> str:
            AsyncProvider.calls += 1
            return "Bearer async-token"

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json=job_payload())

    await make(handler, auth=AsyncProvider()).jobs.get(JOB_ID)
    assert seen == ["Bearer async-token"] and AsyncProvider.calls == 1


async def test_blocking_auth_provider_runs_off_the_event_loop() -> None:
    loop_thread = threading.get_ident()
    used_threads: list[int] = []

    class BlockingProvider:
        def authorization_header(self) -> str:
            used_threads.append(threading.get_ident())
            return "Bearer refreshed"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer refreshed"
        return httpx.Response(200, json=job_payload())

    await make(handler, auth=BlockingProvider()).jobs.get(JOB_ID)
    assert used_threads and used_threads[0] != loop_thread


@pytest.mark.parametrize(("status", "body", "headers", "error"), [
    (401, {"detail": "invalid token"}, {"WWW-Authenticate": "Bearer"}, AuthenticationError),
    (403, {"detail": "missing scope: admin"}, {}, PermissionDeniedError),
    (404, {"detail": "job not found"}, {}, NotFoundError),
    (409, {"detail": "job is done"}, {}, ConflictError),
    (409, {"detail": "Idempotency-Key reused with a different body"}, {}, IdempotencyConflictError),
    (422, {"detail": [{"loc": ["body", "goal"], "msg": "too short"}]}, {}, ValidationError),
    (429, {"detail": "rate limited"}, {"Retry-After": "7"}, RateLimitError),
])
async def test_error_mapping(status, body, headers, error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers)

    client = make(handler, retry=RetryPolicy(max_attempts=1))
    with pytest.raises(error) as caught:
        await client.jobs.cancel(JOB_ID)
    if status == 403:
        assert caught.value.missing_scope == "admin"
    if status == 429:
        assert caught.value.retry_after == 7.0
    if status == 401:
        assert caught.value.www_authenticate == "Bearer"


async def test_get_retries_on_503_honouring_retry_after() -> None:
    calls = []
    sleeper = AsyncSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503, json={"detail": "busy"}, headers={"Retry-After": "2"})
        return httpx.Response(200, json=job_payload("running"))

    job = await make(handler, sleeper=sleeper).jobs.get(JOB_ID)
    assert job.status is JobStatus.RUNNING
    assert len(calls) == 3 and sleeper.calls == [2.0, 2.0]


async def test_plain_post_is_never_retried_but_keyed_post_is() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Idempotency-Key"))
        if len(calls) in (1, 2):
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(200, json={"id": JOB_ID, "quota": None})

    client = make(handler)
    with pytest.raises(ServerError):
        await client.jobs.create("do the thing")
    created = await client.jobs.create("do the thing", idempotency_key="k-1")
    assert created.id == JOB_ID
    assert calls == [None, "k-1", "k-1"]


async def test_network_error_on_get_retries_then_raises() -> None:
    sleeper = AsyncSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(NetworkError, match="failed before a response"):
        await make(handler, sleeper=sleeper, retry=RetryPolicy(max_attempts=3)).jobs.get(JOB_ID)
    assert len(sleeper.calls) == 2


async def test_create_validation_and_run_at() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": JOB_ID})

    client = make(handler)
    with pytest.raises(ValueError):
        await client.jobs.create("x")
    with pytest.raises(ValueError):
        await client.jobs.create("valid goal", run_at="not a date")
    await client.jobs.create("valid goal", run_at="2099-01-01T00:00:00Z")
    assert bodies == [{"goal": "valid goal", "run_at": "2099-01-01T00:00:00Z"}]


async def test_job_list_pagination_follows_cursor() -> None:
    pages = {None: (["a", "b"], "c1"), "c1": (["c"], None)}

    def handler(request: httpx.Request) -> httpx.Response:
        ids, nxt = pages[request.url.params.get("cursor")]
        assert request.url.params["status"] == "queued"
        return httpx.Response(200, json={"jobs": [{**job_payload(), "id": i} for i in ids], "next_cursor": nxt})

    client = make(handler)
    assert [j.id async for j in client.jobs.iter_all(status="queued", limit=2)] == ["a", "b", "c"]
    assert [j.id for j in await client.jobs.list(status="queued")] == ["a", "b"]
    with pytest.raises(ValueError):
        await client.jobs.page(limit=0)


async def test_iter_events_drains_by_sequence_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params["after"])
        stored = [event_dict(1, "queued"), event_dict(2, "running"), event_dict(3, "done")]
        return httpx.Response(200, json={"events": [e for e in stored if e["sequence"] > after][:2]})

    kinds = [e.kind async for e in make(handler).jobs.iter_events(JOB_ID)]
    assert kinds == ["queued", "running", "done"]


async def test_sse_stream_resumes_with_last_event_id_after_drop() -> None:
    seen: list[str | None] = []
    heartbeats: list[str] = []
    sleeper = AsyncSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Last-Event-ID"))
        if len(seen) == 1:
            return sse(sse_frame(1, "queued"), sse_heartbeat(1), sse_frame(2, "running"),
                       exc=httpx.RemoteProtocolError("peer closed", request=request))
        assert request.url.params["after"] == "2"
        return sse(sse_frame(3, "done", {"result": {}}))

    events = [e async for e in make(handler, sleeper=sleeper).jobs.stream_events(JOB_ID, on_heartbeat=heartbeats.append)]
    assert [e.kind for e in events] == ["queued", "running", "done"]
    assert seen == [None, "2"]
    assert heartbeats == ["heartbeat 1"]
    assert sleeper.calls == [1.0]


async def test_sse_stream_closes_client_side_on_cancelled() -> None:
    # The real server heartbeats forever after cancelled; the stream must end on the event.
    def handler(request: httpx.Request) -> httpx.Response:
        return sse(sse_frame(1, "queued"), sse_frame(2, "cancelled"), sse_heartbeat(2),
                   exc=AssertionError("read past the terminal event"))

    assert [e.kind async for e in make(handler).jobs.stream_events(JOB_ID)] == ["queued", "cancelled"]


async def test_sse_budget_error_frame_malformed_and_404() -> None:
    def dropping(request: httpx.Request) -> httpx.Response:
        return sse(sse_frame(1, "running"), exc=httpx.ReadError("dropped", request=request))

    with pytest.raises(NetworkError, match="last delivered event id 1"):
        [e async for e in make(dropping).jobs.stream_events(JOB_ID, max_reconnects=2)]
    with pytest.raises(NetworkError):
        [e async for e in make(dropping).jobs.stream_events(JOB_ID, reconnect=False)]

    def error_frame(request: httpx.Request) -> httpx.Response:
        return sse(b'event: error\ndata: {"detail":"job not found"}\n\n')

    with pytest.raises(StreamError, match="job not found"):
        [e async for e in make(error_frame).jobs.stream_events(JOB_ID)]

    def malformed(request: httpx.Request) -> httpx.Response:
        return sse(b"id: 1\nevent: queued\ndata: not-json\n\n")

    with pytest.raises(StreamError, match="malformed"):
        [e async for e in make(malformed).jobs.stream_events(JOB_ID)]

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "job not found"})

    with pytest.raises(NotFoundError):
        [e async for e in make(missing).jobs.stream_events("missing")]


async def test_wait_polls_to_terminal_and_times_out() -> None:
    states = iter(["queued", "running", "done"])
    sleeper = AsyncSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=job_payload(next(states), result={"ok": True}))

    job = await make(handler, sleeper=sleeper).jobs.wait(JOB_ID, poll_interval=0.5)
    assert job.status is JobStatus.DONE and sleeper.calls == [0.5, 0.5]

    def forever(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=job_payload("running"))

    real_sleep = AsyncMeemeeClient(BASE_URL, auth="t", transport=httpx.MockTransport(forever))
    with pytest.raises(WaitTimeoutError):
        await real_sleep.jobs.wait(JOB_ID, timeout=0.05, poll_interval=0.01)
    with pytest.raises(ValueError):
        await real_sleep.jobs.wait(JOB_ID, poll_interval=0)


async def test_tokens_create_list_revoke() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "t1", "token": "mee_secret"})
        if request.method == "GET":
            return httpx.Response(200, json={"tokens": [{"id": "t1", "name": "ci", "scopes": ["jobs:read"],
                                                         "created_at": NOW, "expires_at": None, "revoked_at": None,
                                                         "last_used_at": None}], "next_cursor": None})
        return httpx.Response(200, json={"id": "t1", "revoked": True})

    client = make(handler)
    with pytest.raises(ValueError):
        await client.tokens.create("ci", {"root"})
    with pytest.raises(ValueError):
        await client.tokens.create("ci", set())
    created = await client.tokens.create("ci", ["jobs:write", "jobs:read"])
    assert created.token == "mee_secret" and bodies[0]["scopes"] == ["jobs:read", "jobs:write"]
    assert [t.id async for t in client.tokens.iter_all()] == ["t1"]
    assert (await client.tokens.revoke("t1")).revoked is True


async def test_audit_iterates_chain_and_validates_limit() -> None:
    def entry(seq: int) -> dict:
        return {"sequence": seq, "occurred_at": NOW, "actor_id": "api", "action": "job.create", "resource": "j",
                "outcome": "success", "metadata": {}, "previous_hash": "0" * 64, "entry_hash": f"{seq:064d}"}

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params["after"])
        entries = [entry(s) for s in (1, 2, 3) if s > after][:2]
        return httpx.Response(200, json={"entries": entries, "verified": True, "next_cursor": None})

    client = make(handler)
    assert [e.sequence async for e in client.audit.iter_entries(limit=2)] == [1, 2, 3]
    with pytest.raises(ValueError):
        await client.audit.list(limit=501)


async def test_quota_approvals_and_webhooks_paths() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        path = request.url.path
        if path.startswith("/v1/quota"):
            return httpx.Response(200, json={"day": "2026-09-24", "used": 0, "limit": 5, "remaining": 5})
        if path.startswith("/v1/approvals") and request.method == "GET":
            return httpx.Response(200, json={"approvals": []})
        if path.startswith("/v1/approvals"):
            return httpx.Response(200, json={"ok": True})
        if path == "/v1/webhooks" and request.method == "GET":
            return httpx.Response(200, json={"webhooks": []})
        if path == "/v1/webhook-deliveries":
            return httpx.Response(200, json={"deliveries": [], "next_cursor": None})
        return httpx.Response(200, json={"deleted": True})

    client = make(handler)
    assert (await client.quota.set("user a", 5)).limit == 5
    with pytest.raises(ValueError):
        await client.quota.set("u", 0)
    assert await client.approvals.list("team/alice") == []
    await client.approvals.grant("team/alice", "shell", argument_constraints={"cmd": "ls"})
    await client.approvals.revoke("team/alice", "fs/write")
    assert await client.webhooks.list() == []
    assert [d async for d in client.webhooks.iter_deliveries()] == []
    with pytest.raises(ValueError):
        await client.webhooks.create("https://x", set())
    # Path encoding is identical to the synchronous client's.
    async_paths = [p for _, p in seen if p.startswith("/v1/approvals")]
    seen.clear()
    from meemee_client import MeemeeClient
    sync = MeemeeClient(BASE_URL, auth="t", transport=httpx.MockTransport(handler))
    sync.approvals.list("team/alice")
    sync.approvals.grant("team/alice", "shell", argument_constraints={"cmd": "ls"})
    sync.approvals.revoke("team/alice", "fs/write")
    assert async_paths == [p for _, p in seen]


async def test_companion_turn_and_validation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"user_id": "u1", "text": "hi", "channel": "local", "conversation_id": "c1"}
        return httpx.Response(200, json={"conversation_id": "c1", "reply": "hello", "facts_learned": 0,
                                         "persona": "warm"})

    client = make(handler)
    with pytest.raises(ValueError):
        await client.companion.chat("u1", "   ")
    reply = await client.companion.chat("u1", "hi", conversation_id="c1")
    assert reply.reply == "hello"


async def test_concurrent_requests_share_one_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**job_payload(), "id": request.url.path.rsplit("/", 1)[-1]})

    client = make(handler)
    jobs = await asyncio.gather(*(client.jobs.get(f"job{i}") for i in range(20)))
    assert [j.id for j in jobs] == [f"job{i}" for i in range(20)]


def test_constructor_validates_base_url() -> None:
    with pytest.raises(ValueError):
        AsyncMeemeeClient("127.0.0.1:8787")

"""401 handling: one forced refresh and one retry for refresh-capable providers."""
from __future__ import annotations

import json

import httpx
import pytest
from conftest import BASE_URL, JOB_ID, FakeSleeper, job_payload, sse_frame
from meemee_client import (
    AsyncMeemeeClient,
    AsyncOIDCClientCredentialsAuth,
    AuthenticationError,
    MeemeeClient,
    RetryPolicy,
    TokenAuth,
)
from test_ws import ScriptedServer, frame, send_then


class Rotating:
    """Refresh-capable provider: each refresh() moves to the next credential."""

    def __init__(self, *tokens: str, fail_refresh: bool = False) -> None:
        self.tokens, self.index, self.refreshes, self.fail_refresh = list(tokens), 0, 0, fail_refresh

    def authorization_header(self) -> str:
        return f"Bearer {self.tokens[min(self.index, len(self.tokens) - 1)]}"

    def refresh(self) -> None:
        if self.fail_refresh:
            raise AuthenticationError("OIDC token request failed: idp down", status_code=0)
        self.refreshes += 1
        self.index += 1


class AsyncRotating(Rotating):
    async def authorization_header_async(self) -> str:
        return self.authorization_header()

    async def refresh(self) -> None:  # type: ignore[override]
        Rotating.refresh(self)


def accepting(good: str, seen: list[httpx.Request], body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {good}":
            return httpx.Response(401, json={"detail": "invalid or expired token"}, headers={"WWW-Authenticate": "Bearer"})
        return httpx.Response(200, json=body if body is not None else job_payload())
    return handler


def sync(handler, auth, retry=None) -> MeemeeClient:
    return MeemeeClient(BASE_URL, auth=auth, transport=httpx.MockTransport(handler), sleeper=FakeSleeper(), retry=retry)


def aclient(handler, auth, retry=None) -> AsyncMeemeeClient:
    async def nosleep(_: float) -> None:
        return None
    return AsyncMeemeeClient(BASE_URL, auth=auth, transport=httpx.MockTransport(handler), sleeper=nosleep, retry=retry)


# ------------------------------------------------------------------ sync HTTP


def test_sync_401_refreshes_once_and_retries_with_new_credential() -> None:
    seen: list[httpx.Request] = []
    auth = Rotating("stale", "fresh")
    assert sync(accepting("fresh", seen), auth).jobs.get(JOB_ID).id == JOB_ID
    assert [r.headers["Authorization"] for r in seen] == ["Bearer stale", "Bearer fresh"]
    assert auth.refreshes == 1


def test_sync_persistent_401_stops_after_one_retry() -> None:
    seen: list[httpx.Request] = []
    auth = Rotating("a", "b", "c", "d")
    with pytest.raises(AuthenticationError) as caught:
        sync(accepting("never", seen), auth).jobs.get(JOB_ID)
    assert len(seen) == 2 and auth.refreshes == 1
    assert caught.value.www_authenticate == "Bearer"


def test_sync_provider_without_refresh_keeps_single_attempt() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(AuthenticationError):
        sync(accepting("never", seen), TokenAuth("static")).jobs.get(JOB_ID)
    assert len(seen) == 1


def test_sync_post_body_is_resent_identically_after_401() -> None:
    seen: list[httpx.Request] = []
    auth = Rotating("stale", "fresh")
    sync(accepting("fresh", seen, {"id": JOB_ID}), auth).jobs.create("a queued goal", run_at="2099-01-01T00:00:00Z")
    assert [r.method for r in seen] == ["POST", "POST"]
    assert json.loads(seen[0].content) == json.loads(seen[1].content) == {"goal": "a queued goal", "run_at": "2099-01-01T00:00:00Z"}


def test_sync_refresh_does_not_consume_retry_attempts() -> None:
    responses = iter([401, 503, 200])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        status = next(responses)
        return httpx.Response(status, json=job_payload() if status == 200 else {"detail": "x"})

    job = sync(handler, Rotating("stale", "fresh"), RetryPolicy(max_attempts=2)).jobs.get(JOB_ID)
    assert job.id == JOB_ID and seen == ["Bearer stale", "Bearer fresh", "Bearer fresh"]


def test_sync_each_request_gets_its_own_single_refresh() -> None:
    auth = Rotating("t0", "t1", "t2")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Every credential is rejected the first time it is seen, then accepted.
        return httpx.Response(401 if calls["n"] in (1, 3) else 200, json=job_payload())

    client = sync(handler, auth)
    client.jobs.get(JOB_ID)
    client.jobs.get(JOB_ID)
    assert auth.refreshes == 2 and calls["n"] == 4


def test_sync_refresh_failure_propagates() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(AuthenticationError, match="idp down"):
        sync(accepting("never", seen), Rotating("stale", fail_refresh=True)).jobs.get(JOB_ID)
    assert len(seen) == 1


def test_sync_sse_stream_reopens_once_after_401() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        if request.headers["Authorization"] != "Bearer fresh":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              content=sse_frame(1, "queued") + sse_frame(2, "done", {"result": {}}))

    auth = Rotating("stale", "fresh")
    events = list(sync(handler, auth).jobs.stream_events(JOB_ID))
    assert [e.kind for e in events] == ["queued", "done"] and seen == ["Bearer stale", "Bearer fresh"]

    seen.clear()
    with pytest.raises(AuthenticationError):
        list(sync(handler, Rotating("x", "y", "z")).jobs.stream_events(JOB_ID))
    assert len(seen) == 2


# ----------------------------------------------------------------- async HTTP


async def test_async_401_refresh_retry_and_persistent_limit() -> None:
    seen: list[httpx.Request] = []
    auth = AsyncRotating("stale", "fresh")
    assert (await aclient(accepting("fresh", seen), auth).jobs.get(JOB_ID)).id == JOB_ID
    assert auth.refreshes == 1 and len(seen) == 2

    seen.clear()
    stuck = AsyncRotating("a", "b", "c")
    with pytest.raises(AuthenticationError):
        await aclient(accepting("never", seen), stuck).jobs.get(JOB_ID)
    assert len(seen) == 2 and stuck.refreshes == 1

    seen.clear()
    with pytest.raises(AuthenticationError):
        await aclient(accepting("never", seen), "static-token").jobs.get(JOB_ID)
    assert len(seen) == 1


async def test_async_sync_style_refresh_and_post_body() -> None:
    seen: list[httpx.Request] = []
    auth = Rotating("stale", "fresh")  # sync refresh() on the async client
    await aclient(accepting("fresh", seen, {"id": JOB_ID}), auth).jobs.create("async goal body")
    assert auth.refreshes == 1
    assert [json.loads(r.content) for r in seen] == [{"goal": "async goal body"}] * 2


async def test_async_sse_stream_reopens_from_scratch_after_401() -> None:
    opened: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        opened.append(request.headers["Authorization"])
        if request.headers["Authorization"] != "Bearer fresh":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              content=sse_frame(1, "queued") + sse_frame(2, "cancelled"))

    kinds = [e.kind async for e in aclient(handler, AsyncRotating("stale", "fresh")).jobs.stream_events(JOB_ID)]
    assert kinds == ["queued", "cancelled"] and opened == ["Bearer stale", "Bearer fresh"]
    opened.clear()
    with pytest.raises(AuthenticationError):
        [e async for e in aclient(handler, AsyncRotating("x", "y", "z")).jobs.stream_events(JOB_ID)]
    assert len(opened) == 2


async def test_async_oidc_provider_refetches_after_server_rejection() -> None:
    fetched = {"n": 0}

    def idp(request: httpx.Request) -> httpx.Response:
        fetched["n"] += 1
        return httpx.Response(200, json={"access_token": f"idp-{fetched['n']}", "token_type": "Bearer", "expires_in": 3600})

    auth = AsyncOIDCClientCredentialsAuth(token_endpoint="http://idp.test/token", client_id="w", client_secret="s",
                                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(idp)))
    seen: list[httpx.Request] = []
    # The API accepts only the second token, as after a server-side key rotation.
    job = await aclient(accepting("idp-2", seen), auth).jobs.get(JOB_ID)
    assert job.id == JOB_ID and fetched["n"] == 2
    assert [r.headers["Authorization"] for r in seen] == ["Bearer idp-1", "Bearer idp-2"]


# ------------------------------------------------------------------ WebSocket


@pytest.fixture()
def server():
    srv = ScriptedServer()
    yield srv
    srv.stop()


def test_sync_ws_4401_refreshes_once_without_using_reconnect_budget(server) -> None:
    server.steps.append(send_then(close=(4401, "authentication required")))
    server.steps.append(send_then(frame(1, "queued"), frame(2, "done")))
    auth = Rotating("stale", "fresh")
    sleeper = FakeSleeper()
    client = MeemeeClient(server.base, auth=auth, sleeper=sleeper)
    events = list(client.jobs.stream_ws(JOB_ID, reconnect=False))
    assert [e.kind for e in events] == ["queued", "done"]
    assert [h["Authorization"] for _, h in server.requests] == ["Bearer stale", "Bearer fresh"]
    assert auth.refreshes == 1 and sleeper.calls == []


def test_sync_ws_persistent_4401_and_static_token(server) -> None:
    for _ in range(3):
        server.steps.append(send_then(close=(4401, "authentication required")))
    with pytest.raises(AuthenticationError):
        list(MeemeeClient(server.base, auth=Rotating("a", "b", "c")).jobs.stream_ws(JOB_ID))
    assert len(server.requests) == 2
    server.requests.clear()
    with pytest.raises(AuthenticationError):
        list(MeemeeClient(server.base, auth="static").jobs.stream_ws(JOB_ID))
    assert len(server.requests) == 1


def test_sync_ws_http_401_handshake_refreshes_once(server) -> None:
    server.http_status = 401
    server.steps.append(send_then(frame(1, "done")))
    auth = Rotating("stale", "fresh")
    assert [e.kind for e in MeemeeClient(server.base, auth=auth).jobs.stream_ws(JOB_ID)] == ["done"]
    assert auth.refreshes == 1 and len(server.requests) == 2


async def test_async_ws_4401_refresh_and_limit(server) -> None:
    server.steps.append(send_then(close=(4401, "authentication required")))
    server.steps.append(send_then(frame(1, "cancelled")))
    auth = AsyncRotating("stale", "fresh")
    client = AsyncMeemeeClient(server.base, auth=auth)
    assert [e.kind async for e in client.jobs.stream_ws(JOB_ID, reconnect=False)] == ["cancelled"]
    assert auth.refreshes == 1
    server.requests.clear()
    for _ in range(3):
        server.steps.append(send_then(close=(4401, "authentication required")))
    with pytest.raises(AuthenticationError):
        [e async for e in AsyncMeemeeClient(server.base, auth=AsyncRotating("a", "b", "c")).jobs.stream_ws(JOB_ID)]
    assert len(server.requests) == 2

"""Live: AsyncMeemeeClient and WebSocket streaming against a booted Meemee server.

Reuses the uvicorn fixture from test_live_integration, so the real server
package, its token store, job queue and WebSocket handler are exercised over
real sockets. Skips when the server package or ``websockets`` is unavailable
(uvicorn needs ``websockets`` to serve WebSocket upgrades at all).
"""
from __future__ import annotations

import pytest
from meemee_client import (
    AsyncMeemeeClient,
    AuthenticationError,
    JobStatus,
    MeemeeClient,
    NotFoundError,
    PermissionDeniedError,
    PersonaConfig,
)
from test_live_integration import SERVER_PACKAGE, live_server  # noqa: F401 - fixture import

pytest.importorskip("websockets")
pytestmark = pytest.mark.skipif(not (SERVER_PACKAGE / "api.py").exists(), reason="meemee server package not found")

BOOT = "bootstrap-test-token"


@pytest.fixture(scope="module")
def reader_and_writer(live_server: str):  # noqa: F811
    with MeemeeClient(live_server, auth=BOOT) as admin:
        rw = admin.tokens.create("live-ws", {"jobs:read", "jobs:write"})
        runs_only = admin.tokens.create("live-ws-noread", {"runs:write"})
    return rw.token, runs_only.token


@pytest.fixture()
def cancelled_job(live_server: str, reader_and_writer) -> str:  # noqa: F811
    with MeemeeClient(live_server, auth=reader_and_writer[0]) as client:
        job = client.jobs.create("Live WebSocket goal", run_at="2099-01-01T00:00:00Z")
        client.jobs.cancel(job.id)
        return job.id


def test_live_sync_ws_replays_and_closes(live_server: str, reader_and_writer, cancelled_job: str) -> None:  # noqa: F811
    with MeemeeClient(live_server, auth=reader_and_writer[0]) as client:
        events = list(client.jobs.stream_ws(cancelled_job, reconnect=False))
        assert [e.kind for e in events] == ["queued", "cancelled"]
        resumed = list(client.jobs.stream_ws(cancelled_job, after=events[0].sequence, reconnect=False))
        assert [e.kind for e in resumed] == ["cancelled"]


def test_live_sync_ws_follows_a_live_job_to_cancellation(live_server: str, reader_and_writer) -> None:  # noqa: F811
    with MeemeeClient(live_server, auth=reader_and_writer[0]) as client:
        job = client.jobs.create("Live follow goal", run_at="2099-01-01T00:00:00Z")
        stream = client.jobs.stream_ws(job.id, reconnect=False)
        first = next(stream)
        assert first.kind == "queued"
        client.jobs.cancel(job.id)  # happens while the socket is open
        rest = list(stream)
        assert [e.kind for e in rest] == ["cancelled"]


def test_live_ws_rejections_reach_the_client_as_typed_errors(live_server: str, reader_and_writer, cancelled_job: str) -> None:  # noqa: F811
    with pytest.raises(AuthenticationError, match="4401"):
        list(MeemeeClient(live_server, auth="mee_not_a_token").jobs.stream_ws(cancelled_job, reconnect=False))
    with pytest.raises(PermissionDeniedError, match="4403") as caught:
        list(MeemeeClient(live_server, auth=reader_and_writer[1]).jobs.stream_ws(cancelled_job, reconnect=False))
    assert caught.value.missing_scope == "jobs:read"
    with pytest.raises(NotFoundError, match="4404"):
        list(MeemeeClient(live_server, auth=reader_and_writer[0]).jobs.stream_ws("no-such-job", reconnect=False))


async def test_live_async_client_surface(live_server: str, reader_and_writer, cancelled_job: str) -> None:  # noqa: F811
    async with AsyncMeemeeClient(live_server, auth=reader_and_writer[0]) as client:
        assert (await client.health()).status == "ok"
        job = await client.jobs.get(cancelled_job)
        assert job.status is JobStatus.CANCELLED
        assert cancelled_job in [j.id async for j in client.jobs.iter_all(limit=1)]
        assert [e.kind async for e in client.jobs.iter_events(cancelled_job)] == ["queued", "cancelled"]
        assert [e.kind async for e in client.jobs.stream_events(cancelled_job)] == ["queued", "cancelled"]
        assert [e.kind async for e in client.jobs.stream_ws(cancelled_job, reconnect=False)] == ["queued", "cancelled"]
        assert (await client.jobs.wait(cancelled_job, timeout=5)).is_terminal
        assert (await client.jobs.cancel(cancelled_job)).status is JobStatus.CANCELLED
        with pytest.raises(NotFoundError):
            await client.jobs.get("missing-job-id")
        with pytest.raises(PermissionDeniedError):
            await client.tokens.create("nope", {"admin"})
        assert client.last_response_info is not None and client.last_response_info.request_id


async def test_live_async_admin_audit_tokens_and_companion(live_server: str) -> None:  # noqa: F811
    async with AsyncMeemeeClient(live_server, auth=BOOT) as admin:
        minted = await admin.tokens.create("live-async", {"jobs:read"})
        assert minted.id in [t.id async for t in admin.tokens.iter_all()]
        page = await admin.audit.list(limit=500)
        assert page.verified and "token.create" in {e.action for e in page.entries}
        assert [e.sequence async for e in admin.audit.iter_entries(limit=3)] == [e.sequence for e in page.entries]
        assert (await admin.tokens.revoke(minted.id)).revoked
        with pytest.raises(AuthenticationError):
            async with AsyncMeemeeClient(live_server, auth=minted.token) as revoked:
                await revoked.jobs.list()
        assert "meemee" in await admin.metrics()
        user = await admin.companion.upsert_user("live-async-user", "Async Tester",
                                                 persona=PersonaConfig(display_name="Mee", tone="calm"))
        assert user.display_name == "Async Tester" and user.persona.display_name == "Mee"
        fact = await admin.companion.add_fact("live-async-user", "Prefers tea over coffee", category="preference")
        assert any(f.id == fact.id for f in await admin.companion.list_facts("live-async-user", query="tea"))


async def test_live_async_ws_follows_live_cancel(live_server: str, reader_and_writer) -> None:  # noqa: F811
    async with AsyncMeemeeClient(live_server, auth=reader_and_writer[0]) as client:
        job = await client.jobs.create("Async follow goal", run_at="2099-01-01T00:00:00Z")
        kinds = []
        async for event in client.jobs.stream_ws(job.id, reconnect=False):
            kinds.append(event.kind)
            if event.kind == "queued":
                await client.jobs.cancel(job.id)
        assert kinds == ["queued", "cancelled"]

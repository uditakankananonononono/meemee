"""Live integration tests: the SDK against a real Meemee server process.

Boots the actual v0.16.0 server (the meemee package next to sdk/ in the repo
tree) on a throwaway port with a bootstrap token, then exercises the full
contract. Skips cleanly when the server package or its dependencies are not
installed - the mocked-transport suite above always runs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from meemee_client import (
    AuthenticationError,
    JobStatus,
    MeemeeClient,
    NotFoundError,
    PermissionDeniedError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PACKAGE = REPO_ROOT / "meemee"

pytestmark = pytest.mark.skipif(
    not (SERVER_PACKAGE / "api.py").exists(),
    reason="meemee server package not found next to sdk/",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory):
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("server dependencies (fastapi, uvicorn) not installed here")
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("meemee-data")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "MEEMEE_API_TOKEN": "bootstrap-test-token",
        "MEEMEE_DATA_DIR": str(data_dir),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"server exited during startup:\n{output}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("server did not become healthy within 20s")
    yield base
    proc.kill()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def admin(live_server: str) -> MeemeeClient:
    with MeemeeClient(live_server, auth="bootstrap-test-token") as client:
        yield client


@pytest.fixture(scope="module")
def scoped_token_id_and_secret(admin: MeemeeClient):
    minted = admin.tokens.create("live-integration", {"jobs:read", "jobs:write"})
    return minted.id, minted.token


@pytest.fixture(scope="module")
def scoped(live_server: str, scoped_token_id_and_secret) -> MeemeeClient:
    _, secret = scoped_token_id_and_secret
    with MeemeeClient(live_server, auth=secret) as client:
        yield client


@pytest.fixture(scope="module")
def cancelled_job_id(scoped: MeemeeClient) -> str:
    created = scoped.jobs.create("Live integration test goal", run_at="2099-01-01T00:00:00Z")
    scoped.jobs.cancel(created.id)
    return created.id


def test_live_health_and_ready(live_server: str) -> None:
    anon = MeemeeClient(live_server)
    health = anon.health()
    assert health.status == "ok"
    assert health.version  # exact package version reported by the server
    assert anon.ready().status == "ready"


def test_live_401_challenge(live_server: str) -> None:
    with pytest.raises(AuthenticationError) as caught:
        MeemeeClient(live_server).jobs.get("whatever")
    assert caught.value.www_authenticate == "Bearer"
    assert caught.value.request_id


def test_live_token_mint_and_scope_denial(admin: MeemeeClient, scoped: MeemeeClient) -> None:
    with pytest.raises(PermissionDeniedError) as caught:
        scoped.tokens.create("nope", {"admin"})
    assert caught.value.missing_scope == "admin"


def test_live_job_lifecycle_and_events(scoped: MeemeeClient, cancelled_job_id: str) -> None:
    job = scoped.jobs.get(cancelled_job_id)
    assert job.status is JobStatus.CANCELLED
    assert job.is_terminal
    kinds = [event.kind for event in scoped.jobs.events(cancelled_job_id)]
    assert kinds == ["queued", "cancelled"]


def test_live_cancel_is_idempotent_for_cancelled_jobs(scoped: MeemeeClient, cancelled_job_id: str) -> None:
    # The real server returns 200 with the current status here; 409 is
    # reserved for done/failed jobs (covered in the mocked suite).
    again = scoped.jobs.cancel(cancelled_job_id)
    assert again.status is JobStatus.CANCELLED


def test_live_unknown_job_is_404(scoped: MeemeeClient) -> None:
    with pytest.raises(NotFoundError):
        scoped.jobs.get("missing-job-id")


def test_live_sse_replay_and_client_side_close(scoped: MeemeeClient, cancelled_job_id: str) -> None:
    # A cancelled job keeps the server stream open (heartbeats); the SDK must
    # close on the terminal cancelled event itself. If it did not, this test
    # would hang instead of failing.
    events = list(scoped.jobs.stream_events(cancelled_job_id))
    assert [event.kind for event in events] == ["queued", "cancelled"]
    assert events[0].sequence < events[1].sequence


def test_live_rate_limit_metadata(scoped: MeemeeClient, cancelled_job_id: str) -> None:
    scoped.jobs.get(cancelled_job_id)
    info = scoped.last_response_info
    assert info is not None and info.request_id
    assert info.rate_limit is not None and info.rate_limit.limit == 60


def test_live_audit_chain_covers_the_session(admin: MeemeeClient, cancelled_job_id: str) -> None:
    page = admin.audit.list(limit=500)
    assert page.verified
    actions = {entry.action for entry in page.entries}
    assert {"token.create", "job.create", "job.cancel"} <= actions
    walked = list(admin.audit.iter_entries(limit=2))
    assert [e.sequence for e in walked] == [e.sequence for e in page.entries]


def test_live_metrics(admin: MeemeeClient) -> None:
    assert "meemee" in admin.metrics()


def test_live_revoke_takes_effect_immediately(admin: MeemeeClient, live_server: str, scoped_token_id_and_secret) -> None:
    token_id, secret = scoped_token_id_and_secret
    assert admin.tokens.revoke(token_id).revoked is True
    with pytest.raises(AuthenticationError):
        MeemeeClient(live_server, auth=secret).jobs.get("anything")

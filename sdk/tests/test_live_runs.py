"""Live: SDK agent runs against a booted Meemee server, worker and model provider.

The model is a real local HTTP server speaking the OpenAI chat-completions
protocol with a fixed two-turn script (the same shape as the server's own
``tests/test_live_runs_e2e.py``): turn one asks for ``workspace.read_file`` on
``note.txt``, turn two answers from the tool result it was sent back. Everything
between the SDK and that provider is production code: uvicorn serving
``meemee.api:app``, the ``meemee worker`` process, the agent loop, the real tool,
the run store and durable job events over SSE and WebSocket.

Runs in SQLite mode always, and in PostgreSQL mode when MEEMEE_TEST_POSTGRES_DSN
(or MEEMEE_TEST_DATABASE_URL) points at a server where the role may CREATE DATABASE.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from meemee_client import (
    AsyncMeemeeClient,
    JobStatus,
    MeemeeClient,
    NotFoundError,
    PermissionDeniedError,
    RunReport,
    ServerError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PACKAGE = REPO_ROOT / "meemee"
PG_DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
NOTE = "pb4 sdk live-run marker 91c2"
FINAL = f"The note says: {NOTE}"
MODEL_KEY = "local-sdk-e2e-key"
ADMIN = "sdk-live-runs-bootstrap"

pytestmark = pytest.mark.skipif(not (SERVER_PACKAGE / "api.py").exists(), reason="meemee server package not found next to sdk/")


class ScriptedProvider(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions with a deterministic two-turn script."""

    requests: ClassVar[list[dict]] = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "body": body})
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != f"Bearer {MODEL_KEY}":
            self.send_response(401)
            self.end_headers()
            return
        last = body["messages"][-1]
        if last["role"] == "user":
            decision = {"thought": "read the note", "tool_call": {"name": "workspace.read_file", "arguments": {"path": "note.txt"}}}
        else:
            result = json.loads(last["content"]).get("result") or {}
            if result.get("ok"):
                decision = {"thought": "done", "final": "The note says: " + result["content"]["content"].strip()}
            else:
                decision = {"thought": "blocked", "final": "BLOCKED: " + str(result.get("error"))}
        data = json.dumps({"id": "cmpl-sdk", "object": "chat.completion", "model": body.get("model"),
                           "choices": [{"index": 0, "finish_reason": "stop",
                                        "message": {"role": "assistant", "content": json.dumps(decision)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_healthy(proc: subprocess.Popen, base: str, log: Path) -> None:
    deadline = time.monotonic() + 30
    while True:
        if proc.poll() is not None:
            pytest.fail(f"server exited during startup:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail(f"server not healthy in 30s:\n{log.read_text()[-3000:]}")
        time.sleep(0.2)


def _boot_server(env: dict, workspace: Path, log: Path) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=workspace, stdout=log.open("w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    _wait_healthy(proc, base, log)
    return proc, base


def _pg_database():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_sdk_e2e_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')

    def drop():
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    return make_conninfo(PG_DSN, dbname=name), drop


@pytest.fixture(scope="module")
def provider():
    ScriptedProvider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL"))]


@pytest.fixture(scope="module", params=BACKENDS)
def stack(request, provider, tmp_path_factory):
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("server dependencies (fastapi, uvicorn) not installed here")
    backend = request.param
    data_dir = tmp_path_factory.mktemp(f"sdk-runs-{backend}-data")
    workspace = tmp_path_factory.mktemp(f"sdk-runs-{backend}-ws")
    (workspace / "note.txt").write_text(NOTE + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "MEEMEE_API_TOKEN": ADMIN,
        "MEEMEE_DATA_DIR": str(data_dir),
        "MEEMEE_WORKSPACE": str(workspace),
        "MEEMEE_MODEL_BASE_URL": provider,
        "MEEMEE_MODEL_NAME": "scripted-sdk",
        "MEEMEE_MODEL_API_KEY": MODEL_KEY,
        "MEEMEE_MODEL_MAX_ATTEMPTS": "1",
        "MEEMEE_WORKER_POLL_SECONDS": "0.2",
        "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "MEEMEE_PERSISTENCE_BACKEND": backend,
        "MEEMEE_RATE_LIMIT_REQUESTS": "10000",
    }
    for key in ("MEEMEE_MODEL_ROUTES", "MEEMEE_MODEL_PROFILES"):
        env.pop(key, None)
    drop = None
    if backend == "postgresql":
        env["MEEMEE_POSTGRES_DSN"], drop = _pg_database()
    server, base = _boot_server(env, workspace, data_dir / "server.log")
    worker_log = data_dir / "worker.log"
    worker = subprocess.Popen([sys.executable, "-c", "from meemee.cli import app; app()", "worker"],
                              env=env, cwd=workspace, stdout=worker_log.open("w"), stderr=subprocess.STDOUT)
    with MeemeeClient(base, auth=ADMIN) as admin:
        owner = admin.tokens.create("sdk-runs-owner", {"runs:write", "jobs:read", "jobs:write"}).token
        other = admin.tokens.create("sdk-runs-other", {"runs:write", "jobs:read", "jobs:write"}).token
        jobs_only = admin.tokens.create("sdk-runs-jobs-only", {"jobs:read", "jobs:write"}).token
    try:
        yield {"base": base, "env": env, "workspace": workspace, "data_dir": data_dir, "worker_log": worker_log,
               "owner": owner, "other": other, "jobs_only": jobs_only}
    finally:
        for proc in (worker, server):
            proc.kill()
            proc.wait(timeout=10)
        if drop:
            drop()


def _assert_report(report: RunReport, goal: str) -> None:
    assert isinstance(report, RunReport)
    assert report.goal == goal
    assert report.final == FINAL
    assert report.steps_used == 2
    [tool] = report.tool_results
    assert tool["tool"] == "workspace.read_file" and tool["result"]["ok"] is True
    assert report.run_id


def _assert_stored(stored: RunReport, report: RunReport) -> None:
    # POST /v1/runs returns the loop's report; the run store stamps created_at on write.
    assert stored.run_id == report.run_id and stored.final == FINAL and stored.steps_used == 2
    assert report.created_at is None and stored.created_at is not None


# ---------------------------------------------------------------- sync client


def test_sync_run_parses_final_answer_and_tool_result(stack) -> None:
    goal = "Read note.txt and tell me what it says (sync SDK)"
    before = len(ScriptedProvider.requests)
    with MeemeeClient(stack["base"], auth=stack["owner"]) as client:
        report = client.runs.create(goal)
        _assert_report(report, goal)
        calls = ScriptedProvider.requests[before:]
        assert len(calls) == 2 and all(c["body"]["model"] == "scripted-sdk" for c in calls)
        assert NOTE in calls[1]["body"]["messages"][-1]["content"]  # tool result reached the model

        _assert_stored(client.runs.get(report.run_id), report)
        assert report.run_id in [r.run_id for r in client.runs.list(limit=50)]
        assert report.run_id in [r.run_id for r in client.runs.iter_all(limit=1)]  # real cursor paging
        assert client.last_response_info.request_id


def test_sync_runs_are_owner_scoped_and_scope_checked(stack) -> None:
    with MeemeeClient(stack["base"], auth=stack["owner"]) as owner:
        run_id = owner.runs.create("Read note.txt please").run_id
    with MeemeeClient(stack["base"], auth=stack["other"]) as other:
        with pytest.raises(NotFoundError):
            other.runs.get(run_id)
        assert run_id not in [r.run_id for r in other.runs.list(limit=100)]
    with MeemeeClient(stack["base"], auth=stack["jobs_only"]) as jobs_only:
        with pytest.raises(PermissionDeniedError) as caught:
            jobs_only.runs.create("Read note.txt without the scope")
        assert caught.value.missing_scope == "runs:write"


def test_sync_job_runs_in_worker_streams_sse_and_ws_to_done(stack) -> None:
    pytest.importorskip("websockets")
    with MeemeeClient(stack["base"], auth=stack["owner"]) as client:
        job = client.jobs.create("Read note.txt and report it (sync job)")
        kinds = [e.kind for e in client.jobs.stream_events(job.id)]
        assert kinds[0] == "queued" and "running" in kinds and kinds[-1] == "done", (
            f"{kinds}\nworker log:\n{stack['worker_log'].read_text()[-3000:]}")
        finished = client.jobs.get(job.id)
        assert finished.status is JobStatus.DONE and finished.is_terminal
        assert finished.result_data["final"] == FINAL
        assert [e.kind for e in client.jobs.stream_ws(job.id, reconnect=False)] == kinds  # durable replay over WS
        assert [e.kind for e in client.jobs.iter_events(job.id)] == kinds
        assert client.jobs.wait(job.id, timeout=5, poll_interval=0.1).status is JobStatus.DONE


# --------------------------------------------------------------- async client


async def test_async_run_parses_final_answer_and_lists(stack) -> None:
    goal = "Read note.txt and tell me what it says (async SDK)"
    async with AsyncMeemeeClient(stack["base"], auth=stack["owner"]) as client:
        report = await client.runs.create(goal)
        _assert_report(report, goal)
        _assert_stored(await client.runs.get(report.run_id), report)
        assert report.run_id in [r.run_id for r in await client.runs.list(limit=50)]
        assert report.run_id in [r.run_id async for r in client.runs.iter_all(limit=1)]
    async with AsyncMeemeeClient(stack["base"], auth=stack["other"]) as other:
        with pytest.raises(NotFoundError):
            await other.runs.get(report.run_id)


async def test_async_job_follows_live_over_ws_and_sse(stack) -> None:
    pytest.importorskip("websockets")
    async with AsyncMeemeeClient(stack["base"], auth=stack["owner"]) as client:
        job = await client.jobs.create("Read note.txt and report it (async job)")
        live = [e async for e in client.jobs.stream_ws(job.id)]  # opened while the job is queued/running
        kinds = [e.kind for e in live]
        assert kinds[0] == "queued" and "running" in kinds and kinds[-1] == "done", (
            f"{kinds}\nworker log:\n{stack['worker_log'].read_text()[-3000:]}")
        assert [e.sequence for e in live] == sorted({e.sequence for e in live})  # once each, in order
        finished = await client.jobs.get(job.id)
        assert finished.status is JobStatus.DONE and finished.result_data["final"] == FINAL
        assert [e.kind async for e in client.jobs.stream_events(job.id)] == kinds


# ---------------------------------------------------------------- error paths


@pytest.fixture(scope="module")
def outage_server(stack):
    """A second server on the same data store whose model endpoint is a dead port."""
    env = dict(stack["env"], MEEMEE_MODEL_BASE_URL=f"http://127.0.0.1:{_free_port()}/v1")
    proc, base = _boot_server(env, stack["workspace"], stack["data_dir"] / "outage.log")
    yield base
    proc.kill()
    proc.wait(timeout=10)


def test_sync_model_outage_raises_server_error_502(stack, outage_server) -> None:
    with MeemeeClient(outage_server, auth=stack["owner"]) as client:
        started = time.monotonic()
        with pytest.raises(ServerError) as caught:
            client.runs.create("Read note.txt during an outage")
        assert caught.value.status_code == 502
        assert "agent run failed" in str(caught.value.detail)
        assert caught.value.request_id
        assert time.monotonic() - started < 30  # POST /v1/runs is never auto-retried


async def test_async_model_outage_raises_server_error_502(stack, outage_server) -> None:
    async with AsyncMeemeeClient(outage_server, auth=stack["owner"]) as client:
        with pytest.raises(ServerError) as caught:
            await client.runs.create("Read note.txt during an outage (async)")
        assert caught.value.status_code == 502
        assert "agent run failed" in str(caught.value.detail)

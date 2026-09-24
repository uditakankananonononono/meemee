"""End-to-end run path against a booted server, in SQLite and PostgreSQL modes.

The model is a real local HTTP server speaking the OpenAI chat-completions protocol,
with a fixed two-turn script: first it asks for ``workspace.read_file``, then it answers
with the file's content taken from the tool result it was sent back. Everything else is the
production path: uvicorn serving ``meemee.api:app``, the ``meemee worker`` process, the
agent loop, the real tool, durable job events over SSE, the run store and the audit chain.

PostgreSQL mode runs when MEEMEE_TEST_POSTGRES_DSN (or MEEMEE_TEST_DATABASE_URL) points at
a server where the role may CREATE DATABASE; ``scripts/pg_live_check.py`` sets that up.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
PG_DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
SECRET_LINE = "pb7 e2e marker 7f3a"
MODEL_KEY = "local-e2e-key"
ADMIN = "e2e-bootstrap-token"


class ScriptedProvider(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions with a deterministic two-turn script."""

    requests: ClassVar[list[dict]] = []

    def log_message(self, *_args):  # keep pytest output clean
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != f"Bearer {MODEL_KEY}":
            self.send_response(401)
            self.end_headers()
            return
        last = body["messages"][-1]
        if last["role"] == "user":
            decision = {"thought": "read the note", "tool_call": {"name": "workspace.read_file", "arguments": {"path": "note.txt"}}}
        else:
            event = json.loads(last["content"])
            result = event.get("result") or {}
            if result.get("ok"):
                decision = {"thought": "done", "final": "The note says: " + result["content"]["content"].strip()}
            else:
                decision = {"thought": "blocked", "final": "BLOCKED: " + str(result.get("error"))}
        payload = {"id": "cmpl-e2e", "object": "chat.completion", "model": body.get("model"),
                   "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(decision)},
                                "finish_reason": "stop"}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def provider():
    ScriptedProvider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _pg_database():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_e2e_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')

    def drop():
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    return make_conninfo(PG_DSN, dbname=name), drop


BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL"))]


@pytest.fixture(scope="module", params=BACKENDS)
def stack(request, provider, tmp_path_factory):
    pytest.importorskip("uvicorn")
    backend = request.param
    data_dir = tmp_path_factory.mktemp(f"e2e-{backend}-data")
    workspace = tmp_path_factory.mktemp(f"e2e-{backend}-ws")
    (workspace / "note.txt").write_text(SECRET_LINE + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "MEEMEE_API_TOKEN": ADMIN,
        "MEEMEE_DATA_DIR": str(data_dir),
        "MEEMEE_WORKSPACE": str(workspace),
        "MEEMEE_MODEL_BASE_URL": provider,
        "MEEMEE_MODEL_NAME": "scripted-e2e",
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
    port = _free_port()
    logs = data_dir / "server.log"
    worker_logs = data_dir / "worker.log"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=workspace, stdout=logs.open("w"), stderr=subprocess.STDOUT)
    worker = subprocess.Popen(
        [sys.executable, "-c", "from meemee.cli import app; app()", "worker"],
        env=env, cwd=workspace, stdout=worker_logs.open("w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        if server.poll() is not None:
            pytest.fail(f"server exited during startup:\n{logs.read_text()}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            server.kill()
            pytest.fail(f"server not healthy in 30s:\n{logs.read_text()}")
        time.sleep(0.2)
    try:
        yield {"base": base, "backend": backend, "workspace": workspace, "env_ref": lambda: env,
               "logs": logs, "worker_logs": worker_logs, "worker": worker}
    finally:
        for proc in (worker, server):
            proc.kill()
            proc.wait(timeout=10)
        if drop:
            drop()


def _headers(token: str = ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scoped_token(base: str) -> str:
    response = httpx.post(f"{base}/v1/tokens", headers=_headers(),
                          json={"name": "e2e", "scopes": ["runs:write", "jobs:read", "jobs:write"]}, timeout=10)
    assert response.status_code in (200, 201), response.text
    return response.json()["token"]


def test_sync_run_uses_model_tool_and_records_run_and_audit(stack, provider):
    base = stack["base"]
    token = _scoped_token(base)
    before = len(ScriptedProvider.requests)
    response = httpx.post(f"{base}/v1/runs", headers=_headers(token),
                          json={"goal": "Read note.txt and tell me what it says"}, timeout=60)
    assert response.status_code == 200, f"{response.text}\n{stack['logs'].read_text()[-3000:]}"
    report = response.json()
    assert report["final"] == f"The note says: {SECRET_LINE}"
    assert report["steps_used"] == 2
    [tool_event] = report["tool_results"]
    assert tool_event["tool"] == "workspace.read_file" and tool_event["result"]["ok"] is True

    calls = ScriptedProvider.requests[before:]
    assert len(calls) == 2
    first = calls[0]["body"]
    assert first["model"] == "scripted-e2e" and first["response_format"] == {"type": "json_object"}
    goal_payload = json.loads(first["messages"][1]["content"])
    assert goal_payload["goal"] == "Read note.txt and tell me what it says"
    assert "workspace.read_file" in {tool["name"] for tool in goal_payload["tools"]}
    assert SECRET_LINE in calls[1]["body"]["messages"][-1]["content"]  # tool result went back to the model

    stored = httpx.get(f"{base}/v1/runs/{report['run_id']}", headers=_headers(token), timeout=10)
    assert stored.status_code == 200 and SECRET_LINE in json.dumps(stored.json())
    listed = httpx.get(f"{base}/v1/runs", headers=_headers(token), timeout=10).json()["runs"]
    assert report["run_id"] in json.dumps(listed)

    audit = httpx.get(f"{base}/v1/audit", headers=_headers(), params={"limit": 500}, timeout=10)
    assert audit.status_code == 200 and audit.json()["verified"] is True
    entries = audit.json()["entries"]
    assert any(e["action"] == "run.create" and e["resource"] == report["run_id"] for e in entries)


def test_run_is_owner_scoped(stack):
    base = stack["base"]
    owner, other = _scoped_token(base), _scoped_token(base)
    report = httpx.post(f"{base}/v1/runs", headers=_headers(owner), json={"goal": "Read note.txt please"}, timeout=60).json()
    assert httpx.get(f"{base}/v1/runs/{report['run_id']}", headers=_headers(other), timeout=10).status_code == 404


def _read_sse(base: str, job_id: str, token: str, timeout: float = 60) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    kind = None
    with httpx.stream("GET", f"{base}/v1/jobs/{job_id}/stream", headers=_headers(token), timeout=timeout) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: ") and kind:
                events.append((kind, json.loads(line[6:])))
                kind = None
    return events


def test_queued_job_runs_in_worker_and_streams_to_completion(stack):
    base = stack["base"]
    token = _scoped_token(base)
    created = httpx.post(f"{base}/v1/jobs", headers=_headers(token), json={"goal": "Read note.txt and report it"}, timeout=10)
    assert created.status_code in (200, 201, 202), created.text
    job_id = created.json()["id"]
    events = _read_sse(base, job_id, token)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "queued" and "running" in kinds and kinds[-1] == "done", (
        f"{kinds}\nworker log:\n{stack['worker_logs'].read_text()[-3000:]}")
    job = httpx.get(f"{base}/v1/jobs/{job_id}", headers=_headers(token), timeout=10).json()
    assert job["status"] == "done"
    result = job["result"] if isinstance(job["result"], dict) else json.loads(job["result"])
    assert result["final"] == f"The note says: {SECRET_LINE}"
    resumed = _read_sse(base, job_id, token)  # stream is replayable from the durable log
    assert [kind for kind, _ in resumed] == kinds


def test_model_outage_returns_502_with_reason(stack):
    base = stack["base"]
    token = _scoped_token(base)
    env = dict(stack["env_ref"](), MEEMEE_MODEL_BASE_URL=f"http://127.0.0.1:{_free_port()}/v1")
    proc, other = _boot(env, stack["workspace"], Path(stack["logs"]).with_name("outage.log"))
    try:
        response = httpx.post(f"{other}/v1/runs", headers=_headers(token), json={"goal": "Read note.txt"}, timeout=60)
        assert response.status_code == 502, response.text
        assert "agent run failed" in response.json()["detail"]
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _boot(env: dict, workspace: Path, log: Path) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=workspace, stdout=log.open("w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        if proc.poll() is not None:
            pytest.fail(f"server exited during startup:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                return proc, base
        except httpx.TransportError:
            time.sleep(0.2)
    proc.kill()
    pytest.fail("server not healthy in 30s")


def test_routed_profiles_fall_back_to_next_provider(stack, provider):
    """Model layer: agent role routed dead-endpoint -> scripted provider, with a key from env."""
    base_env = stack["env_ref"]()
    profiles = [
        {"name": "dead", "base_url": f"http://127.0.0.1:{_free_port()}/v1", "model": "nobody-home", "kind": "self_hosted"},
        {"name": "scripted", "base_url": provider, "model": "scripted-routed", "kind": "self_hosted",
         "api_key_env": "E2E_ROUTED_KEY"},
    ]
    env = dict(base_env, MEEMEE_MODEL_PROFILES=json.dumps(profiles), MEEMEE_MODEL_ROUTES="agent=dead,scripted",
               E2E_ROUTED_KEY=MODEL_KEY, MEEMEE_MODEL_BASE_URL=f"http://127.0.0.1:{_free_port()}/v1")
    proc, base = _boot(env, stack["workspace"], Path(stack["logs"]).with_name("routed.log"))
    try:
        token = _scoped_token(base)
        before = len(ScriptedProvider.requests)
        response = httpx.post(f"{base}/v1/runs", headers=_headers(token), json={"goal": "Read note.txt"}, timeout=90)
        assert response.status_code == 200, response.text
        assert response.json()["final"] == f"The note says: {SECRET_LINE}"
        models = {call["body"]["model"] for call in ScriptedProvider.requests[before:]}
        assert models == {"scripted-routed"}
    finally:
        proc.kill()
        proc.wait(timeout=10)

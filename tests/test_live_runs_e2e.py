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
        goal = json.loads(body["messages"][1]["content"])["goal"]
        writing = goal.startswith("WRITE ")  # "WRITE <path> <content>" asks for the approval-gated tool
        if last["role"] == "user":
            if writing:
                _, path, content = goal.split(" ", 2)
                call = {"name": "workspace.write_file", "arguments": {"path": path, "content": content}}
            else:
                call = {"name": "workspace.read_file", "arguments": {"path": "note.txt"}}
            decision = {"thought": "use the tool", "tool_call": call}
        else:
            event = json.loads(last["content"])
            result = event.get("result") or {}
            if not result.get("ok"):
                decision = {"thought": "blocked", "final": "BLOCKED: " + str(result.get("error"))}
            elif writing:
                decision = {"thought": "done", "final": "WROTE " + result["content"]["path"]}
            else:
                decision = {"thought": "done", "final": "The note says: " + result["content"]["content"].strip()}
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


def _minted(base: str) -> dict:
    response = httpx.post(f"{base}/v1/tokens", headers=_headers(),
                          json={"name": "e2e", "scopes": ["runs:write", "jobs:read", "jobs:write"]}, timeout=10)
    assert response.status_code in (200, 201), response.text
    return response.json()


def _scoped_token(base: str) -> str:
    return _minted(base)["token"]


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


# --- Approval gate on write tools (up-front model: approve per run, or persistent grant) ---

WRITE_TOOL = "workspace.write_file"


def _run(base: str, token: str, goal: str, **extra) -> dict:
    response = httpx.post(f"{base}/v1/runs", headers=_headers(token), json={"goal": goal, **extra}, timeout=60)
    assert response.status_code == 200, response.text
    return response.json()


def _audit(base: str) -> list[dict]:
    response = httpx.get(f"{base}/v1/audit", headers=_headers(), params={"limit": 500}, timeout=10)
    assert response.status_code == 200 and response.json()["verified"] is True, response.text
    return response.json()["entries"]


def test_write_without_approval_is_denied_and_nothing_is_written(stack):
    base, workspace = stack["base"], stack["workspace"]
    report = _run(base, _scoped_token(base), "WRITE out/denied.txt should-not-exist")
    assert report["final"] == "BLOCKED: approval denied for write tool"
    [event] = report["tool_results"]
    assert event["tool"] == WRITE_TOOL and event["result"]["ok"] is False
    assert not (workspace / "out" / "denied.txt").exists()
    # an allowlist for a different tool does not approve this one
    report = _run(base, _scoped_token(base), "WRITE out/denied.txt still-no", approved_tools=["git.commit"])
    assert report["final"].startswith("BLOCKED") and not (workspace / "out" / "denied.txt").exists()


def test_per_run_approval_allows_the_write(stack):
    base, workspace = stack["base"], stack["workspace"]
    token = _scoped_token(base)
    report = _run(base, token, "WRITE out/per-run.txt approved-by-list", approved_tools=[WRITE_TOOL])
    assert report["final"] == "WROTE out/per-run.txt"
    assert (workspace / "out" / "per-run.txt").read_text() == "approved-by-list"
    report = _run(base, token, "WRITE out/switch.txt approved-by-switch", approve_writes=True)
    assert (workspace / "out" / "switch.txt").read_text() == "approved-by-switch"
    entries = _audit(base)
    assert any(e["action"] == "run.create" and e["resource"] == report["run_id"] for e in entries)


def test_persistent_grant_constraints_expiry_and_revoke_with_audit(stack):
    base, workspace = stack["base"], stack["workspace"]
    minted = _minted(base)
    principal, token = minted["id"], minted["token"]
    url = f"{base}/v1/approvals/{principal}"

    granted = httpx.put(url, headers=_headers(), timeout=10,
                        json={"tool": WRITE_TOOL, "argument_constraints": {"path": "granted/ok.txt"}})
    assert granted.status_code == 200, granted.text
    assert _run(base, token, "WRITE granted/ok.txt v1")["final"] == "WROTE granted/ok.txt"
    assert (workspace / "granted" / "ok.txt").read_text() == "v1"
    outside = _run(base, token, "WRITE granted/other.txt nope")
    assert outside["final"].startswith("BLOCKED") and not (workspace / "granted" / "other.txt").exists()
    # grants are per principal: another token gets nothing from this grant
    assert _run(base, _scoped_token(base), "WRITE granted/ok.txt stranger")["final"].startswith("BLOCKED")
    assert (workspace / "granted" / "ok.txt").read_text() == "v1"

    revoked = httpx.delete(f"{url}/{WRITE_TOOL}", headers=_headers(), timeout=10)
    assert revoked.status_code == 200, revoked.text
    assert _run(base, token, "WRITE granted/ok.txt v2")["final"].startswith("BLOCKED")
    assert (workspace / "granted" / "ok.txt").read_text() == "v1"

    expired = httpx.put(url, headers=_headers(), timeout=10,
                        json={"tool": WRITE_TOOL, "expires_at": "2000-01-01T00:00:00+00:00"})
    assert expired.status_code == 200, expired.text
    assert _run(base, token, "WRITE granted/ok.txt v3")["final"].startswith("BLOCKED")
    assert (workspace / "granted" / "ok.txt").read_text() == "v1"

    entries = _audit(base)
    grants = [e for e in entries if e["action"] == "approval.grant" and e["resource"] == principal]
    revokes = [e for e in entries if e["action"] == "approval.revoke" and e["resource"] == principal]
    assert len(grants) == 2 and grants[0]["metadata"]["argument_constraints"] == {"path": "granted/ok.txt"}
    assert grants[1]["metadata"]["expires_at"] == "2000-01-01T00:00:00+00:00"
    assert len(revokes) == 1 and revokes[0]["metadata"]["tool"] == WRITE_TOOL
    assert grants[0]["sequence"] < revokes[0]["sequence"] < grants[1]["sequence"]


def test_unknown_tool_grant_is_rejected(stack):
    base = stack["base"]
    response = httpx.put(f"{base}/v1/approvals/{_minted(base)['id']}", headers=_headers(), timeout=10,
                         json={"tool": "workspace.delete_everything"})
    assert response.status_code in (400, 404, 422), response.text


def _job(base: str, token: str, goal: str) -> dict:
    created = httpx.post(f"{base}/v1/jobs", headers=_headers(token), json={"goal": goal}, timeout=10)
    assert created.status_code in (200, 201, 202), created.text
    job_id = created.json()["id"]
    kinds = [kind for kind, _ in _read_sse(base, job_id, token)]
    assert kinds[-1] == "done", kinds
    job = httpx.get(f"{base}/v1/jobs/{job_id}", headers=_headers(token), timeout=10).json()
    return job["result"] if isinstance(job["result"], dict) else json.loads(job["result"])


def test_queued_jobs_honor_persistent_grants_like_runs(stack):
    """Regression: the worker passed no approval callback, so grants never applied to jobs."""
    base, workspace = stack["base"], stack["workspace"]
    minted = _minted(base)
    denied = _job(base, minted["token"], "WRITE jobs/before-grant.txt x")
    assert denied["final"] == "BLOCKED: approval denied for write tool"
    assert not (workspace / "jobs" / "before-grant.txt").exists()

    granted = httpx.put(f"{base}/v1/approvals/{minted['id']}", headers=_headers(), timeout=10,
                        json={"tool": WRITE_TOOL, "argument_constraints": {"path": "jobs/granted.txt"}})
    assert granted.status_code == 200, granted.text
    assert _job(base, minted["token"], "WRITE jobs/granted.txt from-job")["final"] == "WROTE jobs/granted.txt"
    assert (workspace / "jobs" / "granted.txt").read_text() == "from-job"
    assert _job(base, minted["token"], "WRITE jobs/elsewhere.txt y")["final"].startswith("BLOCKED")
    assert _job(base, _scoped_token(base), "WRITE jobs/granted.txt stranger")["final"].startswith("BLOCKED")
    assert (workspace / "jobs" / "granted.txt").read_text() == "from-job"


def test_refusals_are_machine_readable_on_runs_jobs_and_sdk(stack):
    """A refused write is in approvals_required everywhere a client can read the result, and the
    suggested grant body actually works when submitted as-is."""
    base, workspace = stack["base"], stack["workspace"]
    minted = _minted(base)
    token = minted["token"]

    report = _run(base, token, "WRITE refusals/a.txt hello")
    [refusal] = report["approvals_required"]
    assert refusal["tool"] == WRITE_TOOL and refusal["reason"] == "approval_required"
    assert refusal["grantable"] is True and refusal["step"] == 1
    assert refusal["per_run"] == {"approved_tools": [WRITE_TOOL]}
    assert refusal["persistent_grant"]["argument_constraints"] == {"path": "refusals/a.txt", "content": "hello"}
    stored = httpx.get(f"{base}/v1/runs/{report['run_id']}", headers=_headers(token), timeout=10).json()
    assert stored["approvals_required"] == report["approvals_required"]
    listed = httpx.get(f"{base}/v1/runs", headers=_headers(token), timeout=10).json()["runs"]
    assert next(r for r in listed if r["run_id"] == report["run_id"])["approvals_required"] == [refusal]

    # retry with the per-run body from the refusal: allowed, and nothing refused this time
    retried = _run(base, token, "WRITE refusals/a.txt hello", **refusal["per_run"])
    assert retried["approvals_required"] == [] and (workspace / "refusals" / "a.txt").read_text() == "hello"

    job_result = _job(base, token, "WRITE refusals/job.txt later")
    assert [item["tool"] for item in job_result["approvals_required"]] == [WRITE_TOOL]
    grant = job_result["approvals_required"][0]["persistent_grant"]
    assert httpx.put(f"{base}/v1/approvals/{minted['id']}", headers=_headers(), json=grant, timeout=10).status_code == 200
    job_result = _job(base, token, "WRITE refusals/job.txt later")
    assert job_result["approvals_required"] == [] and (workspace / "refusals" / "job.txt").read_text() == "later"

    clean = _run(base, token, "Read note.txt")
    assert clean["approvals_required"] == []

    sdk = pytest.importorskip("meemee_client")
    with sdk.MeemeeClient(base, auth=token) as client:
        parsed = client.runs.get(report["run_id"])
        assert parsed.is_blocked and parsed.approvals_required[0].persistent_grant["tool"] == WRITE_TOOL
        assert client.runs.get(retried["run_id"]).is_blocked is False


def test_blocked_flag_on_runs_jobs_and_sdk(stack):
    """Status-level visibility: a run or job that refused a tool call says blocked=true at the top
    level (job status stays "done" so existing clients and filters are unchanged)."""
    base = stack["base"]
    token = _minted(base)["token"]
    blocked = _run(base, token, "WRITE blocked/a.txt x")
    clean = _run(base, token, "Read note.txt")
    assert blocked["blocked"] is True and clean["blocked"] is False
    for run in (blocked, clean):
        stored = httpx.get(f"{base}/v1/runs/{run['run_id']}", headers=_headers(token), timeout=10).json()
        assert stored["blocked"] is run["blocked"]
    listed = {r["run_id"]: r["blocked"] for r in
              httpx.get(f"{base}/v1/runs", headers=_headers(token), timeout=10).json()["runs"]}
    assert listed[blocked["run_id"]] is True and listed[clean["run_id"]] is False

    ids = {}
    for name, goal in (("blocked", "WRITE blocked/job.txt y"), ("clean", "Read note.txt")):
        created = httpx.post(f"{base}/v1/jobs", headers=_headers(token), json={"goal": goal}, timeout=10)
        assert created.status_code in (200, 201, 202), created.text
        ids[name] = created.json()["id"]
        assert [kind for kind, _ in _read_sse(base, ids[name], token)][-1] == "done"
    for name, expected in (("blocked", True), ("clean", False)):
        job = httpx.get(f"{base}/v1/jobs/{ids[name]}", headers=_headers(token), timeout=10).json()
        assert job["status"] == "done" and job["blocked"] is expected
        assert isinstance(job["result"], str)  # same wire shape on SQLite and PostgreSQL
    done = httpx.get(f"{base}/v1/jobs", params={"status": "done"}, headers=_headers(token), timeout=10).json()["jobs"]
    flags = {j["id"]: j["blocked"] for j in done}
    assert flags[ids["blocked"]] is True and flags[ids["clean"]] is False
    queued = httpx.post(f"{base}/v1/jobs", headers=_headers(token),
                        json={"goal": "Read note.txt", "run_at": "2999-01-01T00:00:00Z"}, timeout=10)
    assert queued.status_code in (200, 201, 202), queued.text  # no result yet: not blocked
    assert httpx.get(f"{base}/v1/jobs/{queued.json()['id']}", headers=_headers(token),
                     timeout=10).json()["blocked"] is False

    sdk = pytest.importorskip("meemee_client")
    with sdk.MeemeeClient(base, auth=token) as client:
        assert client.runs.get(blocked["run_id"]).blocked is True
        assert client.runs.get(clean["run_id"]).is_blocked is False
        job = client.jobs.get(ids["blocked"])
        assert job.blocked is True and job.is_blocked and job.status == sdk.JobStatus.DONE
        assert client.jobs.get(ids["clean"]).is_blocked is False

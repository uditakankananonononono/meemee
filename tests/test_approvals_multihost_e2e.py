"""Tool grants across processes: API grant -> worker enforcement, including separate hosts.

PostgreSQL mode boots two API servers and a worker that share only the database: each has its
own data directory, as separate hosts would. A grant made through API A must be listed by API B,
honored by B's synchronous runs and by the worker's queued jobs, and a revoke through B must stop
the worker. SQLite mode is the supported single-host shape (API and worker share one data
directory) and gets the same grant -> worker -> revoke check; a second API with its own data
directory shows why SQLite grants cannot span hosts.

Uses the scripted OpenAI-protocol provider from ``tests/test_live_runs_e2e.py``. Only the
bootstrap admin token is used, because API tokens still live in each host's auth.sqlite3.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest
from test_live_runs_e2e import (
    ADMIN,
    MODEL_KEY,
    PG_DSN,
    REPO_ROOT,
    ScriptedProvider,
    _boot,
    _headers,
    _pg_database,
    _read_sse,
)

WRITE_TOOL = "workspace.write_file"


@pytest.fixture(scope="module")
def provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _env(provider_url: str, data_dir, workspace, backend: str, dsn: str | None) -> dict:
    env = {
        **os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_API_TOKEN": ADMIN, "MEEMEE_DATA_DIR": str(data_dir),
        "MEEMEE_WORKSPACE": str(workspace), "MEEMEE_MODEL_BASE_URL": provider_url, "MEEMEE_MODEL_NAME": "scripted-e2e",
        "MEEMEE_MODEL_API_KEY": MODEL_KEY, "MEEMEE_MODEL_MAX_ATTEMPTS": "1", "MEEMEE_WORKER_POLL_SECONDS": "0.2",
        "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "MEEMEE_PERSISTENCE_BACKEND": backend,
        "MEEMEE_RATE_LIMIT_REQUESTS": "10000",
    }
    for key in ("MEEMEE_MODEL_ROUTES", "MEEMEE_MODEL_PROFILES", "MEEMEE_POSTGRES_DSN"):
        env.pop(key, None)
    if dsn:
        env["MEEMEE_POSTGRES_DSN"] = dsn
    return env


class Cluster:
    def __init__(self):
        self.procs: list[subprocess.Popen] = []

    def api(self, env, workspace, log):
        proc, base = _boot(env, workspace, log)
        self.procs.append(proc)
        return base

    def worker(self, env, workspace, log):
        proc = subprocess.Popen([sys.executable, "-c", "from meemee.cli import app; app()", "worker"],
                                env=env, cwd=workspace, stdout=log.open("w"), stderr=subprocess.STDOUT)
        self.procs.append(proc)
        time.sleep(0.5)
        assert proc.poll() is None, log.read_text()[-3000:]

    def stop(self):
        for proc in self.procs:
            proc.kill()
            proc.wait(timeout=10)


def _principal(base: str) -> str:
    response = httpx.get(f"{base}/v1/whoami", headers=_headers(), timeout=10)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _job_final(base: str, goal: str) -> str:
    created = httpx.post(f"{base}/v1/jobs", headers=_headers(), json={"goal": goal}, timeout=10)
    assert created.status_code in (200, 201, 202), created.text
    kinds = [kind for kind, _ in _read_sse(base, created.json()["id"], ADMIN)]
    assert kinds[-1] == "done", kinds
    job = httpx.get(f"{base}/v1/jobs/{created.json()['id']}", headers=_headers(), timeout=10).json()
    return json.loads(job["result"])["final"]


def _run_final(base: str, goal: str) -> str:
    response = httpx.post(f"{base}/v1/runs", headers=_headers(), json={"goal": goal}, timeout=60)
    assert response.status_code == 200, response.text
    return response.json()["final"]


def _grant(base: str, principal: str, path: str):
    response = httpx.put(f"{base}/v1/approvals/{principal}", headers=_headers(), timeout=10,
                         json={"tool": WRITE_TOOL, "argument_constraints": {"path": path}})
    assert response.status_code == 200, response.text


@pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")
def test_postgresql_grants_span_hosts(provider, tmp_path):
    pytest.importorskip("uvicorn")
    dsn, drop = _pg_database()
    workspace = tmp_path / "ws"; workspace.mkdir()
    dirs = {name: tmp_path / name for name in ("host-a", "host-b", "host-w")}
    for path in dirs.values():
        path.mkdir()
    cluster = Cluster()
    try:
        base_a = cluster.api(_env(provider, dirs["host-a"], workspace, "postgresql", dsn), workspace, tmp_path / "a.log")
        base_b = cluster.api(_env(provider, dirs["host-b"], workspace, "postgresql", dsn), workspace, tmp_path / "b.log")
        cluster.worker(_env(provider, dirs["host-w"], workspace, "postgresql", dsn), workspace, tmp_path / "w.log")
        principal = _principal(base_a)
        assert _principal(base_b) == principal

        assert _job_final(base_a, "WRITE mh/pg.txt before").startswith("BLOCKED")
        assert not (workspace / "mh" / "pg.txt").exists()

        _grant(base_a, principal, "mh/pg.txt")
        listed = httpx.get(f"{base_b}/v1/approvals/{principal}", headers=_headers(), timeout=10).json()["approvals"]
        assert [(g["tool"], g["argument_constraints"], g["revoked_at"]) for g in listed] == [(WRITE_TOOL, {"path": "mh/pg.txt"}, None)]

        # Worker on its own host, queued through API B: the grant made on API A applies.
        assert _job_final(base_b, "WRITE mh/pg.txt from-worker") == "WROTE mh/pg.txt"
        assert (workspace / "mh" / "pg.txt").read_text() == "from-worker"
        assert _run_final(base_b, "WRITE mh/pg.txt from-run-b") == "WROTE mh/pg.txt"
        assert _job_final(base_a, "WRITE mh/other.txt x").startswith("BLOCKED")  # constraint still enforced

        revoked = httpx.delete(f"{base_b}/v1/approvals/{principal}/{WRITE_TOOL}", headers=_headers(), timeout=10)
        assert revoked.status_code == 200, revoked.text
        assert _job_final(base_a, "WRITE mh/pg.txt after-revoke").startswith("BLOCKED")
        assert _run_final(base_a, "WRITE mh/pg.txt after-revoke").startswith("BLOCKED")
        assert (workspace / "mh" / "pg.txt").read_text() == "from-run-b"
        for name in ("host-a", "host-b", "host-w"):
            assert not (dirs[name] / "approvals.sqlite3").exists(), f"{name} wrote grants to local disk"
    finally:
        cluster.stop()
        drop()


def test_sqlite_single_host_grants_reach_the_worker_and_do_not_span_hosts(provider, tmp_path):
    pytest.importorskip("uvicorn")
    workspace = tmp_path / "ws"; workspace.mkdir()
    shared, other = tmp_path / "host", tmp_path / "other-host"
    shared.mkdir(); other.mkdir()
    cluster = Cluster()
    try:
        base = cluster.api(_env(provider, shared, workspace, "sqlite", None), workspace, tmp_path / "a.log")
        cluster.worker(_env(provider, shared, workspace, "sqlite", None), workspace, tmp_path / "w.log")
        base_other = cluster.api(_env(provider, other, workspace, "sqlite", None), workspace, tmp_path / "o.log")
        principal = _principal(base)

        _grant(base, principal, "mh/sq.txt")
        assert _job_final(base, "WRITE mh/sq.txt from-worker") == "WROTE mh/sq.txt"
        assert (workspace / "mh" / "sq.txt").read_text() == "from-worker"
        # A second host with its own data directory has no view of that grant.
        assert httpx.get(f"{base_other}/v1/approvals/{principal}", headers=_headers(), timeout=10).json()["approvals"] == []
        assert _run_final(base_other, "WRITE mh/sq.txt other-host").startswith("BLOCKED")

        assert httpx.delete(f"{base}/v1/approvals/{principal}/{WRITE_TOOL}", headers=_headers(), timeout=10).status_code == 200
        assert _job_final(base, "WRITE mh/sq.txt after-revoke").startswith("BLOCKED")
        assert (workspace / "mh" / "sq.txt").read_text() == "from-worker"
    finally:
        cluster.stop()


def test_grant_rejects_a_non_iso_expiry(provider, tmp_path):
    pytest.importorskip("uvicorn")
    workspace = tmp_path / "ws"; workspace.mkdir()
    cluster = Cluster()
    try:
        base = cluster.api(_env(provider, tmp_path, workspace, "sqlite", None), workspace, tmp_path / "a.log")
        response = httpx.put(f"{base}/v1/approvals/someone", headers=_headers(), timeout=10,
                             json={"tool": WRITE_TOOL, "expires_at": "next tuesday"})
        assert response.status_code == 422 and "ISO 8601" in response.text
    finally:
        cluster.stop()

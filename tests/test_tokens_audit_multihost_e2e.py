"""API tokens, accounts and the audit chain across hosts (PostgreSQL mode).

Two API servers share only the PostgreSQL database; each has its own data directory, as
separate hosts would. A token minted on A must work on B, a revoke on B must stop it on A,
an account created on A must log in on B, and both hosts must serve one verified audit chain
containing each other's entries, including under concurrent writes from both.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from itertools import pairwise

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
)

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    server = ThreadingHTTPServer(("127.0.0.1", 0), ScriptedProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider = f"http://127.0.0.1:{server.server_address[1]}/v1"
    dsn, drop = _pg_database()
    workspace = tmp_path_factory.mktemp("mh-ws")
    procs, bases, dirs = [], [], []
    try:
        for name in ("host-a", "host-b"):
            data_dir = tmp_path_factory.mktemp(name)
            env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_API_TOKEN": ADMIN, "MEEMEE_DATA_DIR": str(data_dir),
                   "MEEMEE_WORKSPACE": str(workspace), "MEEMEE_MODEL_BASE_URL": provider, "MEEMEE_MODEL_NAME": "scripted-e2e",
                   "MEEMEE_MODEL_API_KEY": MODEL_KEY, "MEEMEE_MODEL_MAX_ATTEMPTS": "1",
                   "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "MEEMEE_PERSISTENCE_BACKEND": "postgresql",
                   "MEEMEE_POSTGRES_DSN": dsn, "MEEMEE_RATE_LIMIT_REQUESTS": "10000"}
            for key in ("MEEMEE_MODEL_ROUTES", "MEEMEE_MODEL_PROFILES"):
                env.pop(key, None)
            proc, base = _boot(env, workspace, data_dir / "server.log")
            procs.append(proc); bases.append(base); dirs.append(data_dir)
        yield {"a": bases[0], "b": bases[1], "dirs": dirs}
    finally:
        for proc in procs:
            proc.kill(); proc.wait(timeout=10)
        server.shutdown()
        drop()


def _audit(base: str) -> list[dict]:
    entries, cursor = [], 0
    while True:
        response = httpx.get(f"{base}/v1/audit", headers=_headers(), params={"after": cursor, "limit": 500}, timeout=10)
        assert response.status_code == 200 and response.json()["verified"] is True, response.text
        body = response.json()
        entries.extend(body["entries"])
        if not body.get("next_cursor"):
            return entries
        cursor = int(body["next_cursor"])


def test_token_minted_on_a_works_on_b_and_revoke_on_b_applies_on_a(hosts):
    a, b = hosts["a"], hosts["b"]
    minted = httpx.post(f"{a}/v1/tokens", headers=_headers(), timeout=10,
                        json={"name": "cross-host", "scopes": ["jobs:read", "jobs:write"]})
    assert minted.status_code == 200, minted.text
    token, ident = minted.json()["token"], minted.json()["id"]
    assert httpx.get(f"{b}/v1/jobs", headers=_headers(token), timeout=10).status_code == 200
    assert httpx.get(f"{b}/v1/whoami", headers=_headers(token), timeout=10).json()["id"] == ident
    listed = httpx.get(f"{b}/v1/tokens", headers=_headers(), timeout=10).json()["tokens"]
    assert ident in {t["id"] for t in listed}
    introspected = httpx.post(f"{b}/v1/tokens/introspect", headers=_headers(), json={"token": token}, timeout=10).json()
    assert introspected["state"] == "active" and introspected["last_used_at"] is not None

    assert httpx.delete(f"{b}/v1/tokens/{ident}", headers=_headers(), timeout=10).status_code == 200
    assert httpx.get(f"{a}/v1/jobs", headers=_headers(token), timeout=10).status_code == 401
    assert httpx.post(f"{a}/v1/tokens/introspect", headers=_headers(), json={"token": token}, timeout=10).json()["state"] == "revoked"
    for data_dir in hosts["dirs"]:
        assert not (data_dir / "auth.sqlite3").exists() and not (data_dir / "audit.sqlite3").exists(), data_dir


def test_account_created_on_a_logs_in_on_b(hosts):
    a, b = hosts["a"], hosts["b"]
    email = f"mh-{time.time_ns()}@example.com"
    created = httpx.post(f"{a}/v1/accounts/signup", json={"email": email, "password": "correct horse battery", "display_name": "MH"}, timeout=30)
    assert created.status_code == 201, created.text
    login = httpx.post(f"{b}/v1/accounts/login", json={"email": email, "password": "correct horse battery"}, timeout=30)
    assert login.status_code == 200, login.text
    session = login.json()["token"]
    me = httpx.get(f"{a}/v1/account", headers=_headers(session), timeout=10)
    assert me.status_code == 200 and me.json()["email"] == email


def test_one_verified_audit_chain_across_hosts_including_concurrent_writes(hosts):
    a, b = hosts["a"], hosts["b"]
    before = len(_audit(a))

    def mint(base_and_index):
        base, index = base_and_index
        response = httpx.post(f"{base}/v1/tokens", headers=_headers(), timeout=30,
                              json={"name": f"burst-{index}", "scopes": ["jobs:read"]})
        assert response.status_code == 200, response.text
        return response.json()["id"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(mint, [(a if i % 2 else b, i) for i in range(40)]))
    chain_a, chain_b = _audit(a), _audit(b)
    assert chain_a == chain_b
    assert len(chain_a) == before + 40
    created = {e["resource"] for e in chain_a if e["action"] == "token.create"}
    assert set(ids) <= created
    assert all(later["previous_hash"] == earlier["entry_hash"] for earlier, later in pairwise(chain_a))
    # Earlier tests' cross-host actions are in the same chain: minted on A, revoked on B.
    actions = [e["action"] for e in chain_a]
    assert "token.revoke" in actions and "token.introspect" in actions

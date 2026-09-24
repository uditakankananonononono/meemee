"""Operator commands act on the shared PostgreSQL data from any host (PostgreSQL mode).

Before item 187, ``meemee account-export``/``account-import``, ``retention-run`` and
``audit-anchor``/``audit-prune``/``audit-anchor-verify`` only knew the local SQLite files, so in
PostgreSQL mode they refused to run (reading a host's stale files would export nothing, prune
nothing, or certify the wrong chain). Here two API servers share only PostgreSQL and the operator
runs each command as a third host with its own empty data directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN, REPO_ROOT, _headers, _pg_database, _scoped_token

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")
ANCHOR_KEY = "operator-e2e-anchor-key-0123456789abcdef"


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h


def _operator(tmp_path, dsn, *args, **env):
    """``meemee <args>`` on a host that has never served a request: empty data dir, same database."""
    data_dir = tmp_path / "operator"; data_dir.mkdir(exist_ok=True)
    full = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_DATA_DIR": str(data_dir),
            "MEEMEE_PERSISTENCE_BACKEND": "postgresql", "MEEMEE_POSTGRES_DSN": dsn,
            "MEEMEE_AUDIT_ANCHOR_KEY": ANCHOR_KEY, **env}
    result = subprocess.run([sys.executable, "-c", "from meemee.cli import app; app()", *args], env=full,
                            cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False)
    return result


def _sql(dsn, query, params=()):
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        cur = c.execute(query, params)
        return cur.fetchall() if cur.description else cur.rowcount


def _audit(base):
    entries, cursor = [], 0
    while True:
        body = httpx.get(f"{base}/v1/audit", headers=_headers(), params={"after": cursor, "limit": 500}, timeout=10).json()
        assert body["verified"] is True, body
        entries.extend(body["entries"])
        if not body.get("next_cursor"):
            return entries
        cursor = int(body["next_cursor"])


def test_export_on_any_host_sees_jobs_and_runs_made_on_both_and_imports_atomically(hosts, tmp_path):
    a, b = hosts["bases"]; dsn = hosts["dsn"]
    token = _scoped_token(a)
    job = httpx.post(f"{a}/v1/jobs", headers=_headers(token), json={"goal": "export me", "run_at": "2999-01-01T00:00:00Z"}, timeout=30)
    assert job.status_code == 200, job.text
    run = httpx.post(f"{b}/v1/runs", headers=_headers(token), json={"goal": "Read note.txt please"}, timeout=60)
    assert run.status_code == 200, run.text
    principal = _sql(dsn, "SELECT principal FROM meemee_jobs WHERE id=%s", (job.json()["id"],))[0][0]

    out = tmp_path / "export.json"
    exported = _operator(tmp_path, dsn, "account-export", principal, str(out))
    assert exported.returncode == 0, exported.stderr
    report = json.loads(exported.stdout)
    assert report["jobs"] == 1 and report["runs"] == 1, report
    payload = json.loads(out.read_text())["payload"]
    assert payload["jobs"][0]["goal"] == "export me" and payload["runs"][0]["run_id"] == run.json()["run_id"]
    assert json.loads(payload["runs"][0]["tool_results"]) == run.json()["tool_results"]

    # Re-importing into the live database collides and writes nothing.
    before = _sql(dsn, "SELECT count(*) FROM meemee_jobs")[0][0]
    collided = _operator(tmp_path, dsn, "account-import", str(out), "--target-principal", "restored")
    assert collided.returncode != 0 and "collision" in collided.stderr + collided.stdout
    assert _sql(dsn, "SELECT count(*) FROM meemee_jobs")[0][0] == before
    assert _sql(dsn, "SELECT count(*) FROM meemee_runs WHERE principal='restored'")[0][0] == 0

    # A fresh database (a restore target) gets every row, owned by the new principal.
    restore_dsn, drop = _pg_database()
    try:
        imported = _operator(tmp_path, restore_dsn, "account-import", str(out), "--target-principal", "restored")
        assert imported.returncode == 0, imported.stderr
        assert json.loads(imported.stdout)["runs"] == 1
        rows = _sql(restore_dsn, "SELECT principal, goal FROM meemee_jobs")
        assert rows == [("restored", "export me")]
        assert _sql(restore_dsn, "SELECT run_id FROM meemee_runs WHERE principal='restored'") == [(run.json()["run_id"],)]
        again = tmp_path / "again.json"
        assert _operator(tmp_path, restore_dsn, "account-export", "restored", str(again)).returncode == 0
        assert json.loads(again.read_text())["payload"]["runs"][0]["tool_results"] == payload["runs"][0]["tool_results"]
    finally:
        drop()


def test_retention_run_on_any_host_prunes_the_shared_rows_every_host_serves(hosts, tmp_path):
    a, b = hosts["bases"]; dsn = hosts["dsn"]
    token = _scoped_token(a)
    old = httpx.post(f"{a}/v1/runs", headers=_headers(token), json={"goal": "Read note.txt old"}, timeout=60).json()["run_id"]
    new = httpx.post(f"{a}/v1/runs", headers=_headers(token), json={"goal": "Read note.txt new"}, timeout=60).json()["run_id"]
    _sql(dsn, "UPDATE meemee_runs SET created_at = now() - interval '200 days' WHERE run_id=%s", (old,))
    assert httpx.get(f"{b}/v1/runs/{old}", headers=_headers(token), timeout=10).status_code == 200

    result = _operator(tmp_path, dsn, "retention-run")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runs"] >= 1
    assert httpx.get(f"{b}/v1/runs/{old}", headers=_headers(token), timeout=10).status_code == 404
    assert httpx.get(f"{a}/v1/runs/{old}", headers=_headers(token), timeout=10).status_code == 404
    assert httpx.get(f"{b}/v1/runs/{new}", headers=_headers(token), timeout=10).status_code == 200
    assert _audit(a)  # retention never touches the audit chain


def test_anchor_verify_and_prune_the_shared_chain_while_both_hosts_append(hosts, tmp_path):
    a, b = hosts["bases"]; dsn = hosts["dsn"]
    for base in (a, b, a):
        _scoped_token(base)  # each mint appends to the one chain
    chain = _audit(b)
    anchor = tmp_path / "anchor.json"
    made = _operator(tmp_path, dsn, "audit-anchor", str(anchor))
    assert made.returncode == 0, made.stderr
    document = json.loads(anchor.read_text())
    assert document["sequence"] == chain[-1]["sequence"] and document["entry_hash"] == chain[-1]["entry_hash"]

    _scoped_token(b)  # later appends on either host keep the anchor valid
    verified = _operator(tmp_path, dsn, "audit-anchor-verify", str(anchor))
    assert verified.returncode == 0 and json.loads(verified.stdout)["status"] == "pass", verified.stdout + verified.stderr

    def mint(i):
        return _scoped_token((a, b)[i % 2])

    with ThreadPoolExecutor(max_workers=8) as pool:  # prune while both hosts keep appending
        minting = [pool.submit(mint, i) for i in range(16)]
        pruned = _operator(tmp_path, dsn, "audit-prune", str(anchor))
        [f.result() for f in minting]
    assert pruned.returncode == 0, pruned.stderr
    assert json.loads(pruned.stdout)["deleted_entries"] == len([e for e in chain if e["sequence"] <= document["sequence"]])
    after_a, after_b = _audit(a), _audit(b)  # both hosts serve the same, still-verified, pruned chain
    assert after_a == after_b and after_a[0]["sequence"] > document["sequence"]
    assert after_a[0]["previous_hash"] == document["entry_hash"]
    assert len(after_a) >= 17
    assert _operator(tmp_path, dsn, "audit-anchor-verify", str(anchor)).returncode == 0

    forged = {**document, "entry_hash": "f" * 64}
    (tmp_path / "forged.json").write_text(json.dumps(forged))
    assert _operator(tmp_path, dsn, "audit-anchor-verify", str(tmp_path / "forged.json")).returncode == 1
    assert _operator(tmp_path, dsn, "audit-prune", str(tmp_path / "forged.json")).returncode != 0

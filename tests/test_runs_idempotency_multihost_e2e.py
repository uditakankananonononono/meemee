"""Run history and idempotent job submission across hosts (PostgreSQL mode).

With per-host runs.sqlite3 a run finished on host A was a 404 on host B, and with per-host
idempotency.sqlite3 a client retrying POST /v1/jobs with the same Idempotency-Key on another host
got a second job. Two API servers with separate data directories share only PostgreSQL.
"""
from __future__ import annotations

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN, _headers, _scoped_token

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h["bases"]


def test_run_finished_on_a_is_readable_and_listed_on_b_only_for_its_owner(hosts):
    a, b = hosts
    owner, other = _scoped_token(a), _scoped_token(b)
    created = httpx.post(f"{a}/v1/runs", headers=_headers(owner), json={"goal": "Read note.txt please"}, timeout=60)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    stored = httpx.get(f"{b}/v1/runs/{run_id}", headers=_headers(owner), timeout=10)
    assert stored.status_code == 200, stored.text
    assert stored.json()["final"] == created.json()["final"] and stored.json()["tool_results"] == created.json()["tool_results"]
    assert run_id in [r["run_id"] for r in httpx.get(f"{b}/v1/runs", headers=_headers(owner), timeout=10).json()["runs"]]
    assert httpx.get(f"{b}/v1/runs/{run_id}", headers=_headers(other), timeout=10).status_code == 404
    assert run_id not in str(httpx.get(f"{a}/v1/runs", headers=_headers(other), timeout=10).json())


def test_run_history_pages_across_hosts(hosts):
    a, b = hosts
    token = _scoped_token(a)
    made = []
    for i in range(4):
        made.append(httpx.post(f"{(a, b)[i % 2]}/v1/runs", headers=_headers(token),
                               json={"goal": f"Read note.txt #{i}"}, timeout=60).json()["run_id"])
    seen, cursor = [], None
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        body = httpx.get(f"{b}/v1/runs", headers=_headers(token), params=params, timeout=10).json()
        seen.extend(r["run_id"] for r in body["runs"])
        cursor = body.get("next_cursor")
        if not cursor:
            break
    assert seen == list(reversed(made))


def test_idempotent_retry_on_another_host_returns_the_first_job(hosts):
    a, b = hosts
    token = _scoped_token(a)
    body = {"goal": "nightly report", "run_at": "2999-01-01T00:00:00Z"}
    first = httpx.post(f"{a}/v1/jobs", headers={**_headers(token), "Idempotency-Key": "retry-1"}, json=body, timeout=30)
    assert first.status_code == 200, first.text
    retry = httpx.post(f"{b}/v1/jobs", headers={**_headers(token), "Idempotency-Key": "retry-1"}, json=body, timeout=30)
    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]
    changed = httpx.post(f"{b}/v1/jobs", headers={**_headers(token), "Idempotency-Key": "retry-1"},
                         json={**body, "goal": "something else"}, timeout=30)
    assert changed.status_code == 409
    jobs = httpx.get(f"{a}/v1/jobs", headers=_headers(token), timeout=10).json()
    assert [j["id"] for j in jobs["jobs"] if j["goal"] == "nightly report"] == [first.json()["id"]]
    assert httpx.get(f"{b}/v1/quota", headers=_headers(token), timeout=10).json()["used"] == 1


def test_concurrent_same_key_on_both_hosts_creates_exactly_one_job(hosts):
    from concurrent.futures import ThreadPoolExecutor
    a, b = hosts
    token = _scoped_token(a)
    body = {"goal": "race report", "run_at": "2999-01-01T00:00:00Z"}

    def post(i):
        return httpx.post(f"{(a, b)[i % 2]}/v1/jobs", headers={**_headers(token), "Idempotency-Key": "race-1"},
                          json=body, timeout=30)
    with ThreadPoolExecutor(10) as pool:
        responses = list(pool.map(post, range(10)))
    assert all(r.status_code in (200, 409) for r in responses), [r.text for r in responses]
    ids = {r.json()["id"] for r in responses if r.status_code == 200}
    assert len(ids) == 1
    assert all("in progress" in r.json()["detail"] for r in responses if r.status_code == 409)
    jobs = httpx.get(f"{b}/v1/jobs", headers=_headers(token), timeout=10).json()["jobs"]
    assert [j["id"] for j in jobs if j["goal"] == "race report"] == list(ids)
    assert httpx.post(f"{a}/v1/jobs", headers={**_headers(token), "Idempotency-Key": "race-1"},
                      json=body, timeout=30).json()["id"] in ids

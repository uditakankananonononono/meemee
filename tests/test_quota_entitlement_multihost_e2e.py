"""Daily job quotas and plan assignments across hosts (PostgreSQL mode).

With per-host quotas.sqlite3, each of N hosts allowed the full daily limit, so a principal could
submit N times its quota; a plan assigned on one host was not enforced on the others. Here two
API servers with separate data directories share only PostgreSQL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN, _headers

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h["bases"]


def _token(base):
    r = httpx.post(f"{base}/v1/tokens", headers=_headers(), timeout=10,
                   json={"name": "quota", "scopes": ["jobs:read", "jobs:write"]})
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["token"]


def test_daily_quota_holds_across_hosts_under_concurrent_submissions(hosts):
    a, b = hosts
    principal, token = _token(a)
    assert httpx.put(f"{b}/v1/quota/{principal}", headers=_headers(), json={"daily_jobs": 5}, timeout=10).status_code == 200

    def submit(i):
        base = (a, b)[i % 2]
        return httpx.post(f"{base}/v1/jobs", headers=_headers(token), json={"goal": f"job {i}", "run_at": "2999-01-01T00:00:00Z"}, timeout=30).status_code

    with ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(submit, range(12)))
    assert codes.count(200) == 5 and codes.count(429) == 7, codes
    for base in (a, b):
        status = httpx.get(f"{base}/v1/quota", headers=_headers(token), timeout=10).json()
        assert (status["used"], status["limit"], status["remaining"]) == (5, 5, 0)


def test_plan_assigned_on_one_host_is_enforced_on_the_other(hosts):
    a, b = hosts
    principal, token = _token(b)
    assert httpx.get(f"{a}/v1/entitlements", headers=_headers(token), timeout=10).json()["plan"] == "starter"
    assigned = httpx.put(f"{b}/v1/entitlements/{principal}", headers=_headers(), json={"plan": "team"}, timeout=10)
    assert assigned.status_code == 200, assigned.text
    seen = httpx.get(f"{a}/v1/entitlements", headers=_headers(token), timeout=10).json()
    assert seen["plan"] == "team" and seen["limits"]["daily_jobs"] == 1000
    assert httpx.get(f"{a}/v1/quota", headers=_headers(token), timeout=10).json()["limit"] == 1000
    who = httpx.get(f"{a}/v1/whoami", headers=_headers(token), timeout=10).json()
    assert who["entitlement"]["plan"] == "team" and who["entitlement"]["usage"]["daily_jobs"] == 0

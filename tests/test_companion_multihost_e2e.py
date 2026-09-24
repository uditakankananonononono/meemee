"""Companion state across hosts (PostgreSQL mode).

With per-host companion.sqlite3 a companion user created on API host A was a 404 on host B, and
facts, retirements and check-in plans split by host. Two API servers with separate data
directories share only PostgreSQL.
"""
from __future__ import annotations

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


def test_user_facts_and_checkins_made_on_a_are_served_and_changed_on_b(hosts):
    a, b = hosts
    admin = _headers()
    created = httpx.put(f"{a}/v1/companion/users/ana", headers=admin,
                        json={"display_name": "Ana", "timezone": "Asia/Kolkata", "checkins": {"enabled": True}}, timeout=10)
    assert created.status_code == 200, created.text
    assert httpx.get(f"{b}/v1/companion/users/ana", headers=admin, timeout=10).json()["display_name"] == "Ana"
    assert "ana" in [u["user_id"] for u in httpx.get(f"{b}/v1/companion/users", headers=admin, timeout=10).json()["users"]]

    tea = httpx.post(f"{b}/v1/companion/users/ana/facts", headers=admin,
                     json={"category": "food", "text": "likes green tea", "confidence": 0.9}, timeout=10).json()
    httpx.post(f"{a}/v1/companion/users/ana/facts", headers=admin,
               json={"category": "work", "text": "works night shifts", "confidence": 0.7}, timeout=10)
    assert {f["text"] for f in httpx.get(f"{a}/v1/companion/users/ana/facts", headers=admin, timeout=10).json()["facts"]} == \
        {"likes green tea", "works night shifts"}
    assert [f["id"] for f in httpx.get(f"{a}/v1/companion/users/ana/facts", headers=admin, params={"query": "tea"},
                                       timeout=10).json()["facts"]] == [tea["id"]]
    assert httpx.delete(f"{a}/v1/companion/users/ana/facts/{tea['id']}", headers=admin, timeout=10).status_code == 200
    assert httpx.get(f"{b}/v1/companion/users/ana/facts", headers=admin, params={"query": "tea"}, timeout=10).json()["facts"] == []

    planned = httpx.post(f"{a}/v1/companion/users/ana/checkins/plan", headers=admin, timeout=10)
    assert planned.status_code == 201, planned.text
    listed = httpx.get(f"{b}/v1/companion/users/ana/checkins", headers=admin, timeout=10).json()["checkins"]
    assert [c["id"] for c in listed] == [planned.json()["id"]]
    again = httpx.post(f"{b}/v1/companion/users/ana/checkins/plan", headers=admin, timeout=10).json()
    assert again["id"] == planned.json()["id"]  # the same slot is not planned twice across hosts
    off = httpx.put(f"{b}/v1/companion/users/ana/checkins", headers=admin, json={"checkins": {"enabled": False}}, timeout=10).json()
    assert off["cancelled_pending"] == 1
    assert [c["status"] for c in httpx.get(f"{a}/v1/companion/users/ana/checkins", headers=admin, timeout=10).json()["checkins"]] == ["cancelled"]

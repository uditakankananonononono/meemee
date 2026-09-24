"""Personal model across hosts (PostgreSQL mode).

With per-host personal-model.sqlite3 a claim written through API host A was invisible on host B,
a correction on B returned 404 for A's item, and a delete on one host left the claim active on the
other. Two API servers with separate data directories share only PostgreSQL.
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
        yield h


def test_claims_written_on_a_are_corrected_and_deleted_on_b(hosts):
    a, b = hosts["bases"]
    admin = _headers()
    body = {"kind": "preference", "title": "city", "value": "Pune", "confidence": 0.7, "source_id": "chat", "source_record_id": "m1"}
    created = httpx.post(f"{a}/v1/personal-model", headers=admin, json=body, timeout=10)
    assert created.status_code == 201, created.text
    item = created.json()
    assert [i["id"] for i in httpx.get(f"{b}/v1/personal-model", headers=admin, timeout=10).json()["items"]] == [item["id"]]

    fixed = httpx.post(f"{b}/v1/personal-model/{item['id']}/correct", headers=admin, json={"value": "Delhi"}, timeout=10)
    assert fixed.status_code == 200, fixed.text
    again = httpx.post(f"{a}/v1/personal-model/{fixed.json()['id']}/correct", headers=admin, json={"value": "Goa"}, timeout=10)
    assert again.status_code == 200, again.text
    evidence = httpx.get(f"{b}/v1/personal-model/{again.json()['id']}/evidence", headers=admin, timeout=10).json()
    assert evidence["status"] == "active" and evidence["supersedes_id"] == fixed.json()["id"]
    assert [i["value"] for i in httpx.get(f"{a}/v1/personal-model", headers=admin, timeout=10).json()["items"]] == ["Goa"]
    history = httpx.get(f"{b}/v1/personal-model", headers=admin, params={"include_history": True}, timeout=10).json()["items"]
    assert {i["value"] for i in history} == {"Pune", "Delhi", "Goa"}

    assert httpx.delete(f"{b}/v1/personal-model/{again.json()['id']}", headers=admin, timeout=10).status_code == 200
    assert httpx.get(f"{a}/v1/personal-model", headers=admin, timeout=10).json()["items"] == []
    assert httpx.delete(f"{a}/v1/personal-model/{again.json()['id']}", headers=admin, timeout=10).status_code == 404


def test_both_hosts_report_the_shared_personal_model_and_context_stores(hosts):
    for base in hosts["bases"]:
        ready = httpx.get(f"{base}/ready", timeout=10).json()["components"]
        assert ready["context"] == {"ok": True} and ready["personal_model"] == {"ok": True}, ready

"""Account-deletion ledger across hosts (PostgreSQL mode).

With per-host account-deletions.sqlite3 a deletion run through API host A was a 404 on host B's
deletion-status route, an interrupted deletion was resumed only by the host that started it, and a
run host B finished after the account was deleted on A was kept. Two API servers with separate
data directories share only PostgreSQL.
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


def test_deletion_run_on_a_is_reported_by_b(hosts):
    a, b = hosts["bases"]
    admin = _headers()
    purged = httpx.delete(f"{a}/v1/admin/principals/ext-user-1/data", headers=admin, timeout=20)
    assert purged.status_code == 200, purged.text
    deletion_id = purged.json()["deletion_id"]
    status = httpx.get(f"{b}/v1/admin/account-deletions/{deletion_id}", headers=admin, timeout=10)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "completed" and status.json()["principal"] == "ext-user-1"
    assert set(status.json()["steps"]) == set(purged.json()["steps"])


def test_interrupted_deletion_is_visible_to_every_host(hosts):
    """An in-progress deletion opened by one process is in the shared ledger that every host resumes from."""
    from meemee_persist_pg import Database, DeletionLedger

    db = Database(hosts["dsn"], min_size=1, max_size=2)
    try:
        ledger = DeletionLedger(db)
        ident = ledger.open("ext-user-2", "operator")
        ledger.record_step(ident, "jobs", {"jobs": 0})
    finally:
        db.close()
    admin = _headers()
    for base in hosts["bases"]:
        record = httpx.get(f"{base}/v1/admin/account-deletions/{ident}", headers=admin, timeout=10).json()
        assert record["status"] == "in_progress" and record["steps"] == {"jobs": {"jobs": 0}}
        assert httpx.get(f"{base}/ready", timeout=10).json()["components"]["account_deletions"] == {"ok": True}

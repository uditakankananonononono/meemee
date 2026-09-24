"""Account-deletion ledger: one contract, SQLite and PostgreSQL."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.account_deletion import DeletionLedger as SQLiteLedger
from meemee_persist_pg.interfaces import DeletionLedgerInterface


@pytest.fixture(params=BACKENDS)
def ledger(request, tmp_path):
    if request.param == "sqlite":
        yield SQLiteLedger(tmp_path / "d.sqlite3")
        return
    from meemee_persist_pg import Database, DeletionLedger, MigrationStore

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=10)
    MigrationStore(db).apply()
    try:
        yield DeletionLedger(db)
    finally:
        db.close(); drop()


def test_ledger_contract(ledger):
    assert isinstance(ledger, DeletionLedgerInterface) and ledger.ping()
    before = "2000-01-01T00:00:00+00:00"
    first = ledger.open("p1", "admin")
    assert ledger.open("p1", "someone-else") == first  # one open deletion per principal
    assert [r["id"] for r in ledger.incomplete()] == [first]
    ledger.record_step(first, "jobs", {"jobs": 2})
    ledger.record_step(first, "jobs", {"jobs": 3})  # a retried step replaces its counts
    ledger.record_step(first, "runs", {"runs": 1})
    assert ledger.done_steps(first) == {"jobs": {"jobs": 3}, "runs": {"runs": 1}}
    ledger.complete(first)
    record = ledger.get(first)
    assert record["status"] == "completed" and record["completed_at"] and record["requested_by"] == "admin"
    assert record["steps"]["jobs"] == {"jobs": 3} and ledger.incomplete() == []
    assert ledger.deleted_since("p1", before) and not ledger.deleted_since("p1", "9999") and not ledger.deleted_since("p2", before)
    second = ledger.open("p1", "admin")
    assert second != first and ledger.get("missing") is None


def test_concurrent_open_yields_one_deletion(ledger):
    with ThreadPoolExecutor(8) as pool:
        ids = set(pool.map(lambda _: ledger.open("p", "admin"), range(16)))
    assert len(ids) == 1 and len(ledger.incomplete()) == 1

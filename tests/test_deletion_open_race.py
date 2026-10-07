"""Independent SQLite deletion ledgers must open one active deletion per owner."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from meemee.account_deletion import DeletionLedger


def test_independent_deletion_open_returns_one_active_id(tmp_path):
    ledgers = [DeletionLedger(tmp_path / 'deletions.sqlite3') for _ in range(2)]
    barrier = Barrier(2)
    for ledger in ledgers:
        ledger.db.set_trace_callback(lambda sql, db=ledger.db: barrier.wait(timeout=2)
                                     if sql.startswith('SELECT id FROM account_deletions') and not db.in_transaction else None)
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda ledger: ledger.open('owner', 'admin'), ledgers))
    assert ids[0] == ids[1]
    assert len(ledgers[0].incomplete()) == 1

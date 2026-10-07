"""Deletion status and step receipts represent one ledger snapshot."""

from pathlib import Path

from test_deletion_ledger_backends import ledger  # noqa: F401


def test_deletion_get_status_and_steps_snapshot(ledger, monkeypatch):  # noqa: F811
    ident = ledger.open('owner', 'admin')
    if hasattr(ledger, 'lock'):
        from meemee.account_deletion import DeletionLedger

        writer = DeletionLedger(Path(ledger.db.execute('PRAGMA database_list').fetchone()[2]))
        changed = []
        def race(sql):
            if 'SELECT step,counts' in sql and not changed:
                changed.append(True)
                writer.record_step(ident, 'jobs', {'jobs': 1})
                writer.complete(ident)
        ledger.db.set_trace_callback(race)
    else:
        from contextlib import contextmanager

        from meemee_persist_pg.account_deletion import DeletionLedger

        writer = DeletionLedger(ledger.db)
        original = ledger.db.transaction
        changed = []
        class Proxy:
            def __init__(self, c):
                self.c = c
            def execute(self, sql, args=()):
                if 'SELECT step,counts' in sql and not changed:
                    changed.append(True)
                    writer.record_step(ident, 'jobs', {'jobs': 1})
                    writer.complete(ident)
                return self.c.execute(sql, args)
        @contextmanager
        def raced_transaction(**kw):
            with original(**kw) as c:
                yield Proxy(c)
        monkeypatch.setattr(ledger.db, 'transaction', raced_transaction)
    result = ledger.get(ident)
    assert (result['status'] == 'in_progress' and result['steps'] == {}) or (
        result['status'] == 'completed' and result['steps'] == {'jobs': {'jobs': 1}})

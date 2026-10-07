"""Independent processes must not race upgrading old approval schemas."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from meemee.approvals import ApprovalStore


def test_legacy_approval_upgrade_is_serialized(tmp_path,monkeypatch):
    path=tmp_path/'grants.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE tool_approvals(principal TEXT NOT NULL, tool TEXT NOT NULL,
            granted_at TEXT NOT NULL,expires_at TEXT,revoked_at TEXT,granted_by TEXT NOT NULL,
            PRIMARY KEY(principal,tool))''')
    connect=sqlite3.connect
    barrier=threading.Barrier(2)
    def traced(*args,**kwargs):
        db=connect(*args,**kwargs)
        def trace(sql):
            if sql.startswith('ALTER TABLE tool_approvals ADD COLUMN'):
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
        db.set_trace_callback(trace)
        return db
    monkeypatch.setattr(sqlite3,'connect',traced)
    with ThreadPoolExecutor(2) as pool:
        stores=list(pool.map(lambda _:ApprovalStore(path),range(2)))
    for store in stores:
        store.grant('owner','effect','owner',argument_constraints={'path':'safe'})
        assert store.allows('owner','effect',arguments={'path':'safe'})
        store.db.close()

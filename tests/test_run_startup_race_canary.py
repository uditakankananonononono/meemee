"""Concurrent run-store startup must serialize the additive refusal column."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from meemee.runs import RunStore


def test_independent_run_store_startups_serialize_schema_edit(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    original = sqlite3.connect
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        def trace(sql):
            if sql.startswith('ALTER TABLE runs ADD COLUMN approvals_required'):
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
        connection.set_trace_callback(trace)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(lambda _: RunStore(tmp_path / 'runs.db'), range(2)))
    for store in stores:
        assert store.ping()
        store.db.close()

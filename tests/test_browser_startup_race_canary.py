"""Concurrent store startup must not race the legacy host-id migration."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from meemee.browser_sessions import BrowserSessionStore


def test_two_startups_cannot_duplicate_host_id_column(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        def sync_alter(statement):
            if statement.startswith('ALTER TABLE browser_sessions ADD COLUMN host_id'):
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
        connection.set_trace_callback(sync_alter)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', traced_connect)
    path = tmp_path / 'sessions.db'
    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(lambda _: BrowserSessionStore(path), range(2)))
    for store in stores:
        columns = [row['name'] for row in store.db.execute('PRAGMA table_info(browser_sessions)')]
        assert columns.count('host_id') == 1
        store.db.close()

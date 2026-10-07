"""Independent monitor handles must share one atomic fire budget."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.monitors import MonitorInput, MonitorStore


def test_independent_monitor_writers_do_not_duplicate_last_fire(tmp_path, monkeypatch):
    path = tmp_path / 'm.db'
    stores = [MonitorStore(path), MonitorStore(path)]
    row = stores[0].create('o', MonitorInput(name='fixture', source_id='s', field='x', operator='eq', expected=1))
    original = MonitorStore.matches

    def slow_match(predicate, event):
        time.sleep(0.04)
        return original(predicate, event)

    monkeypatch.setattr(MonitorStore, 'matches', staticmethod(slow_match))
    ready = threading.Barrier(2)

    def evaluate(store):
        ready.wait()
        return store.evaluate('o', 's', {'x':1})

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(evaluate, stores))
    assert sum(map(len, results)) == 1
    events = stores[0].events('o', row['id'])
    assert sum(e['kind'] == 'triggered' for e in events) == 1

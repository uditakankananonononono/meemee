"""Quota changes and consumption must have one serialized authorization order."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.quotas import QuotaStore


def test_limit_reduction_does_not_commit_before_stale_consumption(tmp_path, monkeypatch):
    stores = [QuotaStore(tmp_path / 'q.db', default_daily_jobs=2), QuotaStore(tmp_path / 'q.db', default_daily_jobs=2)]
    stores[0].consume_job('owner')
    read_limit = threading.Event()
    original = stores[0].limit

    def pause_after_read(principal):
        maximum = original(principal)
        read_limit.set()
        time.sleep(0.15)
        return maximum

    monkeypatch.setattr(stores[0], 'limit', pause_after_read)

    def reduce():
        stores[1].set_limit('owner', 1)
        return stores[1].status('owner')['used']

    with ThreadPoolExecutor(max_workers=2) as pool:
        consumed = pool.submit(stores[0].consume_job, 'owner')
        assert read_limit.wait(2)
        reduced = pool.submit(reduce)
        assert consumed.result(timeout=3)['used'] == 2
        # Reduction may follow an in-flight consumption, but not precede its publication.
        assert reduced.result(timeout=3) == 2

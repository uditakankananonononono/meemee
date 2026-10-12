"""Every shared SQLite connection read must use the store's reentrant lock."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from meemee.entitlements import EntitlementStore
from meemee.quotas import QuotaExceeded, QuotaStore


class CheckedConnection:
    def __init__(self, connection, lock):
        self.connection = connection
        self.lock = lock

    def execute(self, *args):
        assert self.lock._is_owned(), "shared connection accessed outside store lock"
        return self.connection.execute(*args)

    def __enter__(self):
        return self.connection.__enter__()

    def __exit__(self, *args):
        return self.connection.__exit__(*args)


def test_all_entitlement_and_quota_reads_hold_connection_lock(tmp_path):
    e = EntitlementStore(tmp_path / "e.sqlite")
    q = QuotaStore(tmp_path / "q.sqlite")
    e.db = CheckedConnection(e.db, e.lock)
    q.db = CheckedConnection(q.db, q.lock)
    assert e.get("p")["plan"] == "starter"
    assert e.assign("p", "team", "now")["plan"] == "team"
    assert e.allows("p", "webhooks", 0) and e.ping()
    q.set_limit("p", 2)
    assert q.limit("p") == 2 and q.ping()
    assert q.consume_job("p")["used"] == 1
    assert q.status("p")["used"] == 1
    e.delete_principal("p")
    q.delete_principal("p")


def test_concurrent_quota_consumers_and_readers_preserve_exact_limit(tmp_path):
    q = QuotaStore(tmp_path / "q.sqlite")
    q.set_limit("p", 10)
    barrier = threading.Barrier(30)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def submit(_):
        barrier.wait()
        q.status("p", now)
        q.ping()
        try:
            q.consume_job("p", now)
            return "accepted"
        except QuotaExceeded:
            return "refused"

    with ThreadPoolExecutor(max_workers=30) as pool:
        outcomes = list(pool.map(submit, range(30)))
    assert outcomes.count("accepted") == 10
    assert outcomes.count("refused") == 20
    assert q.status("p", now)["used"] == 10

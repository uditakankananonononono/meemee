"""A SQLite claim lease starts after both process and DB writer locks."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from meemee import idempotency


@pytest.mark.parametrize('boundary', ['process', 'database'])
def test_idempotency_claim_clock_after_lock_wait(tmp_path, monkeypatch, boundary):
    clock = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    sampled = Event()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            sampled.set()
            return clock[0]

    monkeypatch.setattr(idempotency, 'datetime', Clock)
    path = tmp_path / 'keys.db'
    store = idempotency.IdempotencyStore(path)
    if boundary == 'process':
        original = store.lock

        class DelayedLock:
            def __enter__(self):
                original.acquire()
                clock[0] += timedelta(minutes=10)

            def __exit__(self, *args):
                original.release()

        store.lock = DelayedLock()
        store.claim('owner', '/jobs', 'key', {}, claim_token='fresh')
    else:
        blocked = Event()
        store.db.set_trace_callback(lambda sql: blocked.set() if sql.startswith(('BEGIN', 'DELETE')) else None)
        other = sqlite3.connect(path)
        other.execute('BEGIN IMMEDIATE')
        try:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(store.claim, 'owner', '/jobs', 'key', {}, claim_token='fresh')
                try:
                    assert blocked.wait(2)
                    assert not sampled.is_set(), 'clock sampled before database writer lock'
                    clock[0] += timedelta(minutes=10)
                finally:
                    other.rollback()
                assert future.result(timeout=2) is None
        finally:
            other.close()
    row = store.db.execute('SELECT created_at, expires_at FROM idempotency').fetchone()
    assert datetime.fromisoformat(row['created_at']) == clock[0]
    assert datetime.fromisoformat(row['expires_at']) == clock[0] + idempotency.CLAIM_LEASE
    assert not store.db.in_transaction

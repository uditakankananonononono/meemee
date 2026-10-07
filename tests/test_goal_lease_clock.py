"""Lease duration starts after lock acquisition, not before a contended wait."""

from datetime import datetime, timedelta, timezone

import pytest

from meemee import goals


@pytest.mark.parametrize('operation', ['claim', 'renew'])
def test_goal_lease_clock_sampled_after_lock_wait(tmp_path, monkeypatch, operation):
    clock = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(goals, '_now', lambda: clock[0])
    store = goals.GoalStore(tmp_path / 'goals.sqlite3')
    goal = store.create('owner', 'work')
    if operation == 'renew':
        store.claim('owner', 'worker', lease_seconds=300)
    original = store.lock

    class DelayedLock:
        waited = False

        def __enter__(self):
            original.acquire()
            if not self.waited:
                clock[0] += timedelta(seconds=100)
                self.waited = True

        def __exit__(self, *args):
            original.release()

    store.lock = DelayedLock()
    row = store.claim('owner', 'worker', lease_seconds=1) if operation == 'claim' else store.renew('owner', goal['id'], 'worker', 1)
    assert datetime.fromisoformat(row['lease_until']) == clock[0] + timedelta(seconds=1)


def test_goal_transition_checks_expiry_after_lock_wait(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(goals, '_now', lambda: clock[0])
    store = goals.GoalStore(tmp_path / 'goals.sqlite3')
    goal = store.create('owner', 'work')
    store.claim('owner', 'worker', lease_seconds=1)
    original = store.lock

    class DelayedLock:
        waited = False

        def __enter__(self):
            original.acquire()
            if not self.waited:
                clock[0] += timedelta(seconds=100)
                self.waited = True

        def __exit__(self, *args):
            original.release()

    store.lock = DelayedLock()
    with pytest.raises(goals.GoalConflict, match='expired'):
        store.transition('owner', goal['id'], 'completed', worker_id='worker')
    assert store.get('owner', goal['id'])['status'] == 'active'
    assert not store.db.in_transaction


@pytest.mark.parametrize('operation', ['claim', 'renew', 'transition'])
def test_goal_clock_waits_for_independent_sqlite_writer(tmp_path, monkeypatch, operation):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    clock = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(goals, '_now', lambda: clock[0])
    path = tmp_path / 'goals.sqlite3'
    store = goals.GoalStore(path)
    goal = store.create('owner', 'work')
    if operation != 'claim':
        store.claim('owner', 'worker', lease_seconds=50)
    blocked, sampled = Event(), Event()
    store.db.set_trace_callback(lambda sql: blocked.set() if sql == 'BEGIN IMMEDIATE' else None)

    def now():
        sampled.set()
        return clock[0]

    monkeypatch.setattr(goals, '_now', now)
    other = sqlite3.connect(path)
    other.execute('BEGIN IMMEDIATE')

    def act():
        if operation == 'claim':
            return store.claim('owner', 'worker', lease_seconds=1)
        if operation == 'renew':
            return store.renew('owner', goal['id'], 'worker', 1)
        return store.transition('owner', goal['id'], 'completed', worker_id='worker')

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(act)
            try:
                assert blocked.wait(2), 'writer did not reach SQLite lock boundary'
                assert not sampled.is_set(), 'lease clock sampled before database writer lock'
                clock[0] += timedelta(seconds=100)
            finally:
                other.rollback()
            if operation == 'claim':
                assert datetime.fromisoformat(future.result(timeout=2)['lease_until']) == clock[0] + timedelta(seconds=1)
            else:
                with pytest.raises(goals.GoalConflict, match='expired|live lease'):
                    future.result(timeout=2)
    finally:
        other.close()
    assert not store.db.in_transaction

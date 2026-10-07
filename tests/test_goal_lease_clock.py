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

"""Concurrent pollers must atomically choose the same pending check-in."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from meemee.companion.checkins import CheckInScheduler
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


def test_independent_planners_reuse_one_pending_slot(tmp_path):
    path = tmp_path / 'c.db'
    stores = [CompanionStore(path), CompanionStore(path)]
    stores[0].upsert_user(UserProfile(user_id='o', display_name='Owner', checkins=CheckInPreferences(enabled=True, cadence_minutes=30)))
    barrier = threading.Barrier(2)
    for store in stores:
        original = store.list_checkins

        def synchronized(user, status=None, limit=50, read=original):
            rows = read(user, status, limit)
            if status == 'running':
                barrier.wait()
            return rows

        store.list_checkins = synchronized
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def plan(i):
        return CheckInScheduler(stores[i]).plan_user('o', start + timedelta(minutes=i))

    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(plan, range(2)))
    assert rows[0]['id'] == rows[1]['id']

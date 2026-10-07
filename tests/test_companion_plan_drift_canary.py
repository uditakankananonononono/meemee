"""Worker poll time must not create a new cadence slot every minute."""
from datetime import datetime, timedelta, timezone

from meemee.companion.checkins import CheckInScheduler
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


def test_polling_reuses_pending_cadence_slot(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    store.upsert_user(UserProfile(user_id='o', display_name='Owner', checkins=CheckInPreferences(enabled=True, cadence_minutes=30)))
    scheduler = CheckInScheduler(store)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    slots = [scheduler.plan_user('o', start + timedelta(minutes=i)) for i in range(10)]
    assert len({row['id'] for row in slots}) == 1
    assert len(store.list_checkins('o', status='queued')) == 1

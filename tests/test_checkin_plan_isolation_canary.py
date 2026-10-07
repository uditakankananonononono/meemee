"""One undeliverable user's quiet window must not stop other owners' planning."""
from datetime import datetime, timezone

from meemee.companion.checkins import CheckInScheduler
from meemee.companion.models import CheckInPreferences, QuietHours, UserProfile
from meemee.companion.store import CompanionStore


def test_whole_day_quiet_owner_does_not_abort_plan_all(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    store.upsert_user(UserProfile(user_id='quiet',display_name='Quiet',checkins=CheckInPreferences(enabled=True,quiet_hours=QuietHours(start='00:00',end='00:00'))))
    store.upsert_user(UserProfile(user_id='ready',display_name='Ready',checkins=CheckInPreferences(enabled=True)))
    planned = CheckInScheduler(store).plan_all(datetime(2026,1,1,tzinfo=timezone.utc))
    assert [row['user_id'] for row in planned] == ['ready']
    assert store.list_checkins('quiet') == []

"""Planning must not silently drop owners beyond the UI list cap."""
from datetime import datetime, timezone

from meemee.companion.checkins import CheckInScheduler
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


def test_planning_includes_owner_beyond_five_hundred(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    store.upsert_user(UserProfile(user_id='old',display_name='Old',checkins=CheckInPreferences(enabled=True)))
    for i in range(500):
        store.upsert_user(UserProfile(user_id=f'user{i}',display_name='Off'))
    planned = CheckInScheduler(store).plan_all(datetime(2026,1,1,tzinfo=timezone.utc))
    assert [row['user_id'] for row in planned] == ['old']

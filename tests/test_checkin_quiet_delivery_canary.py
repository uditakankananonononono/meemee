"""Overdue queued check-ins must not deliver during current quiet hours."""
from test_companion_worker import queue_checkin, setup

from meemee.companion.models import QuietHours
from meemee.companion.worker import deliver_due_once


async def test_overdue_checkin_is_silent_in_current_quiet_window(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    profile = store.profile('udita')
    # Whole-day quiet window, independent of wall clock, to reproduce overdue behavior.
    profile.checkins.quiet_hours = QuietHours(start='00:00', end='00:00')
    store.upsert_user(profile)
    result = await deliver_due_once(store, engine, channels)
    assert not result['delivered']
    assert store.latest_conversation('udita', 'local') is None

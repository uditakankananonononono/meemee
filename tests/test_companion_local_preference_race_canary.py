"""The atomic local commit must honor preferences changed after generation checks."""
import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.worker import deliver_due_once


@pytest.mark.asyncio
@pytest.mark.parametrize('prefs', [CheckInPreferences(enabled=False),
                                  CheckInPreferences(enabled=True, channel='webhook', address='https://example.com')])
async def test_local_preference_change_at_commit_prevents_publication(tmp_path, prefs):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    commit = store.finish_local_checkin

    def changed_preferences(checkin_id, conversation_id, message):
        store.upsert_user(UserProfile(user_id='udita', display_name='Udita', checkins=prefs))
        return commit(checkin_id, conversation_id, message)

    store.finish_local_checkin = changed_preferences
    summary = await deliver_due_once(store, engine, channels)
    assert summary['delivered'] is False
    assert summary['status'] == 'cancelled'
    conv = store.latest_conversation('udita', 'local')
    assert store.history(conv['id']) == []
    assert store.list_checkins('udita')[0]['status'] == 'cancelled'

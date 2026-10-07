"""The final local transaction must not accept a different channel/destination."""

from datetime import datetime, timezone

import pytest

from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


@pytest.mark.parametrize('case', ['conversation_channel', 'checkin_channel', 'configured_address'])
def test_local_commit_rejects_unbound_destination(tmp_path, case):
    store = CompanionStore(tmp_path / 'companion.sqlite3')
    prefs = CheckInPreferences(enabled=True, address='configured' if case == 'configured_address' else None)
    store.upsert_user(UserProfile(user_id='owner', display_name='Owner', checkins=prefs))
    conversation = store.start_conversation('owner', 'webhook' if case == 'conversation_channel' else 'local', 'other')
    row, _ = store.schedule_checkin(
        'owner', datetime.now(timezone.utc), 'slot',
        'webhook' if case == 'checkin_channel' else 'local', prefs.address,
    )
    store.claim_checkin()
    with pytest.raises(ValueError, match='eligible'):
        store.finish_local_checkin(row['id'], conversation['id'], 'misrouted')
    assert store.history(conversation['id']) == []
    assert store.list_checkins('owner')[0]['status'] == 'running'

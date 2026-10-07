"""PG local delivery message and completion must share the same commit."""

from datetime import datetime, timezone

import pytest
from test_companion_backends import backend, store  # noqa: F401

from meemee.companion.channels import LocalChannel
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.worker import deliver_due_once


@pytest.mark.asyncio
async def test_local_completion_failure_does_not_publish_message(store):  # noqa: F811
    store.upsert_user(UserProfile(user_id='owner', display_name='Owner', checkins=CheckInPreferences(enabled=True)))
    row, _ = store.schedule_checkin('owner', datetime.now(timezone.utc), 'slot', 'local', None)
    if hasattr(store, 'lock'):
        store.db.execute("""CREATE TRIGGER reject_done BEFORE UPDATE OF status ON companion_checkins
            WHEN NEW.status='done' BEGIN SELECT RAISE(ABORT,'completion rejected'); END""")
    else:
        with store.db.transaction() as c:
            c.execute("ALTER TABLE meemee_companion_checkins ADD CONSTRAINT reject_done CHECK(status <> 'done')")

    class Engine:
        async def checkin_message(self, user):
            return 'atomic message'

    try:
        await deliver_due_once(store, Engine(), {'local': LocalChannel(store)})
    except Exception as exc:  # noqa: BLE001 - intentional backend failure may propagate
        assert 'completion rejected' in str(exc) or 'reject_done' in str(exc)
    conversation = store.latest_conversation('owner', 'local')
    assert store.history(conversation['id']) == []
    assert store.list_checkins('owner')[0]['id'] == row['id']

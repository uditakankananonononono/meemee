"""Preference revocation during generation must stop the pending send."""
import asyncio

from test_companion_worker import queue_checkin, setup

from meemee.companion.models import CheckInPreferences
from meemee.companion.worker import deliver_due_once


async def test_disable_during_generation_prevents_delivery(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(user):
        entered.set()
        await release.wait()
        return 'fixture message'

    engine.checkin_message = blocked
    task = asyncio.create_task(deliver_due_once(store, engine, channels))
    await entered.wait()
    profile = store.profile('udita')
    profile.checkins = CheckInPreferences(enabled=False)
    store.upsert_user(profile)
    store.cancel_pending_checkins('udita')
    release.set()
    result = await task
    assert result['delivered'] is False
    assert store.latest_conversation('udita', 'local') is None
    assert store.list_checkins('udita')[0]['status'] == 'cancelled'

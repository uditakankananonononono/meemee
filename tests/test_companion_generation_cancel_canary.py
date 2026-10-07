"""Cancellation before delivery must release the claimed check-in."""
import asyncio

import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.worker import deliver_due_once


async def test_generation_cancellation_releases_claim(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    started = asyncio.Event()

    async def blocked(user):
        started.set()
        await asyncio.Event().wait()

    engine.checkin_message = blocked
    task = asyncio.create_task(deliver_due_once(store, engine, channels))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.list_checkins('udita')[0]['status'] == 'queued'

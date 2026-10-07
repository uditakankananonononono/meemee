"""Interrupted adapter calls have unknown outcomes and must not be retried."""
import asyncio

import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.worker import deliver_due_once


async def test_cancel_during_send_is_terminal_unknown(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    entered = asyncio.Event()

    class Interrupted:
        async def send(self, address, message):
            entered.set()
            await asyncio.Event().wait()

    channels['local'] = Interrupted()
    task = asyncio.create_task(deliver_due_once(store, engine, channels))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = store.list_checkins('udita')[0]
    assert row['status'] == 'failed'
    assert 'unknown' in row['last_error']
    assert await deliver_due_once(store, engine, channels) == {'claimed': False}


async def test_unexpected_send_error_after_effect_is_terminal_unknown(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    calls = []

    class Interrupted:
        async def send(self, address, message):
            calls.append(message)  # Simulates provider accepting before socket error.
            raise OSError('reply lost after acceptance')

    channels['local'] = Interrupted()
    try:
        await deliver_due_once(store, engine, channels)
    except OSError:
        pass
    row = store.list_checkins('udita')[0]
    assert row['status'] == 'failed'
    assert 'unknown' in row['last_error']
    assert await deliver_due_once(store, engine, channels) == {'claimed': False}
    assert len(calls) == 1

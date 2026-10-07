"""Interrupted notice sends must not be automatically replayed after the lease."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from meemee.browser_notices import TakeoverNoticeQueue
from meemee.browser_sessions import BrowserSessionStore


@pytest.mark.parametrize('cancel', [True, False])
async def test_interrupted_notice_send_terminal_unknown(tmp_path, cancel):
    sessions = BrowserSessionStore(tmp_path / 's.db', 'fixture')
    queue = TakeoverNoticeQueue(tmp_path / 'n.db')
    sessions.create_session('s', 'o', None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    sessions.create_takeover('t', 's', 'fixture-token', 'captcha', 'agent', expires)
    queue.enqueue({'takeover_id': 't', 'reason': 'captcha', 'expires_at': expires.isoformat(),
                   'url': 'https://fixture.invalid/t'}, 's', 'o')
    entered = asyncio.Event()
    sends = []

    class Companion:
        def profile(self, user):
            return SimpleNamespace(checkins=SimpleNamespace(channel='fixture', address='dest'))

    class Interrupted:
        async def send(self, address, text):
            sends.append(text)
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise OSError('reply lost after acceptance')

    channels = {'fixture': Interrupted()}
    task = asyncio.create_task(queue.deliver_pending(sessions, Companion(), channels))
    await entered.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        try:
            await task
        except OSError:
            pass
    row = queue.db.execute('SELECT text,status,detail FROM browser_takeover_notices').fetchone()
    assert row['status'] == 'failed'
    assert row['text'] is None
    assert 'unknown' in row['detail']
    # Force expiry to prove this terminal row cannot be reclaimed.
    with queue.db:
        queue.db.execute("UPDATE browser_takeover_notices SET lease_until='2000-01-01T00:00:00+00:00'")
    assert await queue.deliver_pending(sessions, Companion(), channels) == []
    assert len(sends) == 1

"""Concurrent SQLite dispatchers must not both send one pending notice."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from meemee.browser_notices import TakeoverNoticeQueue
from meemee.browser_sessions import BrowserSessionStore
from meemee.companion.channels import DeliveryResult


async def test_independent_dispatchers_claim_once(tmp_path):
    sessions = BrowserSessionStore(tmp_path / 's.db', 'fixture')
    queues = [TakeoverNoticeQueue(tmp_path / 'n.db'), TakeoverNoticeQueue(tmp_path / 'n.db')]
    sessions.create_session('s', 'o', None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    sessions.create_takeover('t', 's', 'token', 'captcha', 'agent', expires)
    queues[0].enqueue({'takeover_id':'t','reason':'captcha','expires_at':expires.isoformat(),'url':'https://fixture.invalid'}, 's', 'o')
    started, release = asyncio.Event(), asyncio.Event()
    sent = []

    class Companion:
        def profile(self, user):
            return SimpleNamespace(checkins=SimpleNamespace(channel='fixture',address='dest'))

    class Channel:
        async def send(self, address, text):
            sent.append(text)
            started.set()
            await release.wait()
            return DeliveryResult('fixture', address, True, 'sent')

    channels = {'fixture':Channel()}
    first = asyncio.create_task(queues[0].deliver_pending(sessions, Companion(), channels))
    await started.wait()
    second = asyncio.create_task(queues[1].deliver_pending(sessions, Companion(), channels))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert len(sent) == 1

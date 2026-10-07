"""Takeover tokens must not be erased after a declined delivery."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from meemee.browser_notices import TakeoverNoticeQueue
from meemee.browser_sessions import BrowserSessionStore
from meemee.companion.channels import DeliveryResult


async def test_declined_notice_delivery_keeps_queued_text(tmp_path):
    sessions = BrowserSessionStore(tmp_path / 's.db', 'fixture')
    queue = TakeoverNoticeQueue(tmp_path / 'n.db')
    sessions.create_session('s', 'o', None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    sessions.create_takeover('t', 's', 'fixture-token', 'captcha', 'agent', expires)
    queue.enqueue({'takeover_id':'t','reason':'captcha','expires_at':expires.isoformat(),'url':'https://fixture.invalid/t'}, 's', 'o')

    class Companion:
        def profile(self, user):
            return SimpleNamespace(checkins=SimpleNamespace(channel='fixture', address='dest'))

    class Declined:
        async def send(self, address, text):
            return DeliveryResult('fixture', address, False, 'declined')

    result = await queue.deliver_pending(sessions, Companion(), {'fixture':Declined()})
    assert result[0]['status'] == 'queued'
    row = queue.db.execute('SELECT text,status FROM browser_takeover_notices').fetchone()
    assert row['status'] == 'queued' and 'https://fixture.invalid/t' in row['text']

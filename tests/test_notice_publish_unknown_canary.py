"""Completion write failure after acceptance must not requeue a notice."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from meemee.browser_notices import TakeoverNoticeQueue
from meemee.browser_sessions import BrowserSessionStore
from meemee.companion.channels import DeliveryResult


async def test_accepted_notice_with_failed_completion_no_replay(tmp_path):
    sessions = BrowserSessionStore(tmp_path / 's.db', 'fixture')
    queue = TakeoverNoticeQueue(tmp_path / 'n.db')
    sessions.create_session('s', 'o', None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    sessions.create_takeover('t', 's', 'fixture-token', 'captcha', 'agent', expires)
    queue.enqueue({'takeover_id': 't', 'reason': 'captcha', 'expires_at': expires.isoformat(),
                   'url': 'https://fixture.invalid/t'}, 's', 'o')
    sends = []
    original_finish = queue._finish

    def lose_completion(ident, status, *args, **kwargs):
        if status == 'delivered':
            raise RuntimeError('completion publication unavailable')
        return original_finish(ident, status, *args, **kwargs)

    queue._finish = lose_completion

    class Companion:
        def profile(self, user):
            return SimpleNamespace(checkins=SimpleNamespace(channel='fixture', address='dest'))

    class Accepted:
        async def send(self, address, text):
            sends.append(text)
            return DeliveryResult('fixture', address, True, 'accepted')

    channels = {'fixture': Accepted()}
    result = await queue.deliver_pending(sessions, Companion(), channels)
    assert result[0]['status'] == 'failed'
    assert result[0]['delivered'] is True
    row = queue.db.execute('SELECT text,status,detail FROM browser_takeover_notices').fetchone()
    assert row['status'] == 'failed' and row['text'] is None
    assert 'unknown' in row['detail']
    assert await queue.deliver_pending(sessions, Companion(), channels) == []
    assert len(sends) == 1

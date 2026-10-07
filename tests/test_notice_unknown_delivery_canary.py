"""Unknown HTTP acceptance must not requeue a takeover token notice."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from meemee.browser_notices import TakeoverNoticeQueue
from meemee.browser_sessions import BrowserSessionStore
from meemee.companion.channels import DeliveryOutcomeUnknown


async def test_unknown_notice_send_is_terminal_without_token_replay(tmp_path):
    sessions = BrowserSessionStore(tmp_path / 's.db', 'fixture')
    queue = TakeoverNoticeQueue(tmp_path / 'n.db')
    sessions.create_session('s', 'o', None, [])
    expires = datetime.now(timezone.utc) + timedelta(minutes=5)
    sessions.create_takeover('t', 's', 'fixture-token', 'captcha', 'agent', expires)
    queue.enqueue({'takeover_id': 't', 'reason': 'captcha', 'expires_at': expires.isoformat(),
                   'url': 'https://fixture.invalid/t'}, 's', 'o')
    sends = []

    class Companion:
        def profile(self, user):
            return SimpleNamespace(checkins=SimpleNamespace(channel='fixture', address='dest'))

    class LostReply:
        async def send(self, address, text):
            sends.append(text)
            raise DeliveryOutcomeUnknown('provider acceptance unknown: reply lost')

    channels = {'fixture': LostReply()}
    result = await queue.deliver_pending(sessions, Companion(), channels)
    assert result[0]['status'] == 'failed'
    row = queue.db.execute('SELECT text,status,detail FROM browser_takeover_notices').fetchone()
    assert row['text'] is None
    assert 'unknown' in row['detail']
    assert await queue.deliver_pending(sessions, Companion(), channels) == []
    assert len(sends) == 1

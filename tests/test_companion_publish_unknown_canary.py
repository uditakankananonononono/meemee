"""A successful send followed by lost completion publication must not resend."""
import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.channels import DeliveryResult
from meemee.companion.worker import deliver_due_once


@pytest.mark.parametrize('error', [RuntimeError, OSError])
async def test_sent_then_finish_error_terminal_unknown(tmp_path, error):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    sent = []

    class Accepted:
        async def send(self, address, message):
            sent.append(message)
            return DeliveryResult('local', address, True, 'accepted')

    def failed_publication(checkin_id, message):
        raise error('completion publication unavailable')

    store.finish_checkin_claim = failed_publication
    channels['local'] = Accepted()
    try:
        summary = await deliver_due_once(store, engine, channels)
    except OSError:
        summary = None
    row = store.list_checkins('udita')[0]
    assert row['status'] == 'failed'
    assert 'unknown' in row['last_error']
    assert summary['delivered'] is True
    assert summary['status'] == 'failed'
    assert await deliver_due_once(store, engine, channels) == {'claimed': False}
    assert len(sent) == 1


async def test_fallback_publication_failure_never_requeues(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    sent = []

    class Accepted:
        async def send(self, address, message):
            sent.append(message)
            return DeliveryResult('local', address, True, 'accepted')

    def unavailable(*args):
        raise RuntimeError('all publication unavailable')

    store.finish_checkin_claim = unavailable
    store.unknown_checkin_claim = unavailable
    channels['local'] = Accepted()
    with pytest.raises(RuntimeError, match='all publication unavailable'):
        await deliver_due_once(store, engine, channels)
    assert store.list_checkins('udita')[0]['status'] == 'running'
    assert await deliver_due_once(store, engine, channels) == {'claimed': False}
    assert len(sent) == 1

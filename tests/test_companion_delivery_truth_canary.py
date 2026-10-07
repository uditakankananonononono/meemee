"""Unsuccessful delivery must not be marked done or stranded running."""
from test_companion_worker import queue_checkin, setup

from meemee.companion.channels import DeliveryResult
from meemee.companion.worker import deliver_due_once


async def test_negative_delivery_result_does_not_mark_done(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)

    class Declined:
        async def send(self, address, message):
            return DeliveryResult('local', address, False, 'provider declined')

    channels['local'] = Declined()
    summary = await deliver_due_once(store, engine, channels)
    assert not summary['delivered']
    assert store.list_checkins('udita')[0]['status'] == 'queued'


async def test_unexpected_model_exception_does_not_strand_running(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)

    async def broken(user):
        raise OSError('fixture transport error')

    engine.checkin_message = broken
    try:
        await deliver_due_once(store, engine, channels)
    except OSError:
        pass
    assert store.list_checkins('udita')[0]['status'] == 'queued'

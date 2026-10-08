"""Standalone companion loop must release the HTTP clients it constructs."""

import asyncio
from types import SimpleNamespace

import pytest

from meemee.companion import runtime, worker


@pytest.mark.parametrize('channel_close_fails', [False, True])
def test_companion_loop_closes_model_and_all_channels_on_cancel(monkeypatch, channel_close_fails):
    closed = []

    class Client:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)
            if self.name == 'failing' and channel_close_fails:
                raise RuntimeError('close failure')

    companion = SimpleNamespace(store=object(), engine=object(), model=Client('model'),
                                channels={'plain': object(), 'good': Client('good'), 'failing': Client('failing')})
    monkeypatch.setattr(runtime, 'build_companion', lambda settings: companion)
    monkeypatch.setattr(worker, 'CheckInScheduler', lambda store: SimpleNamespace(plan_all=lambda: None))

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'deliver_due_once', cancel)
    with pytest.raises(RuntimeError if channel_close_fails else asyncio.CancelledError):
        asyncio.run(worker.checkin_forever(SimpleNamespace(companion_checkin_poll_seconds=1)))
    assert set(closed) == {'model', 'good', 'failing'}


def test_standalone_companion_actual_http_clients_are_closed(monkeypatch, tmp_path):
    from meemee.config import Settings

    created = []
    original = runtime.build_companion

    def capture(settings):
        companion = original(settings)
        created.append(companion)
        return companion

    monkeypatch.setattr(runtime, 'build_companion', capture)
    monkeypatch.setattr(worker, 'CheckInScheduler', lambda store: SimpleNamespace(plan_all=lambda: None))

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'deliver_due_once', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.checkin_forever(Settings(data_dir=tmp_path, persistence_backend='sqlite')))
    companion = created[0]
    assert companion.model.client.is_closed
    assert all(channel.client.is_closed for channel in companion.channels.values() if hasattr(channel, 'client'))

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

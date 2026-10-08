"""Idle job-worker cancellation must release its owned persistence resources."""

import asyncio
from types import SimpleNamespace

import pytest

from meemee import worker


async def test_idle_worker_cancellation_closes_persistence(monkeypatch):
    closed = []
    stores = SimpleNamespace(jobs=SimpleNamespace(claim=lambda: None), webhooks=object(), approvals=object(),
                             close=lambda: closed.append('persistence'))
    monkeypatch.setattr(worker, 'persistence_from_settings', lambda settings: stores)
    async def cancelled_sleep(delay):
        raise asyncio.CancelledError
    monkeypatch.setattr(worker.asyncio, 'sleep', cancelled_sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker.work_forever(SimpleNamespace(worker_poll_seconds=1))
    assert closed == ['persistence']

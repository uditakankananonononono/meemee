"""Idle job-worker cancellation must release its owned persistence resources."""

import asyncio
from types import SimpleNamespace

import pytest
from test_token_audit_backends import persistence  # noqa: F401

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


async def test_worker_startup_error_closes_created_persistence(monkeypatch):
    closed = []
    class BrokenStores:
        @property
        def jobs(self):
            raise RuntimeError('jobs unavailable')
        def close(self):
            closed.append('persistence')
    monkeypatch.setattr(worker, 'persistence_from_settings', lambda settings: BrokenStores())
    with pytest.raises(RuntimeError, match='jobs unavailable'):
        await worker.work_forever(SimpleNamespace())
    assert closed == ['persistence']


async def test_idle_worker_releases_real_backend_resources(persistence, monkeypatch, tmp_path):  # noqa: F811
    import sqlite3

    from meemee.config import Settings

    monkeypatch.setattr(worker, 'persistence_from_settings', lambda settings: persistence)
    async def cancelled_sleep(delay):
        raise asyncio.CancelledError
    monkeypatch.setattr(worker.asyncio, 'sleep', cancelled_sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker.work_forever(Settings(_env_file=None, data_dir=tmp_path))
    if persistence.backend == 'sqlite':
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            persistence.jobs.db.execute("SELECT 1")
    else:
        assert persistence.database.pool.closed is True

"""Standalone companion owns its PG pool; API-borrowed compositions do not."""

import asyncio
from types import SimpleNamespace

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee import persistence
from meemee.companion import runtime, worker
from meemee.config import Settings


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_standalone_companion_releases_actual_owned_pg_pool(monkeypatch, tmp_path):
    dsn, drop = _pg_dsn()
    created = []
    original = persistence.persistence_from_settings

    def capture(settings):
        value = original(settings)
        created.append(value)
        return value

    monkeypatch.setattr(persistence, 'persistence_from_settings', capture)
    monkeypatch.setattr(worker, 'CheckInScheduler', lambda store: SimpleNamespace(plan_all=lambda: None))

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'deliver_due_once', cancel)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.checkin_forever(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn)))
        assert created[0].database.pool.closed
    finally:
        for value in created:
            value.close()
        drop()


async def test_borrowed_persistence_is_not_owned_by_companion(tmp_path):
    shared = persistence.build_persistence('sqlite', tmp_path)
    companion = await runtime.build_companion_async(Settings(_env_file=None, data_dir=tmp_path), persistence=shared)
    try:
        assert companion.owned_persistence is None
        assert shared.jobs.db.execute('SELECT 1').fetchone()[0] == 1
    finally:
        await companion.model.aclose()
        for channel in companion.channels.values():
            if hasattr(channel, 'aclose'):
                await channel.aclose()


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_standalone_loop_does_not_close_borrowed_actual_pg_pool(monkeypatch, tmp_path):
    dsn, drop = _pg_dsn()
    shared = persistence.build_persistence('postgresql', tmp_path, dsn)
    original = runtime.build_companion_async
    monkeypatch.setattr(runtime, 'build_companion_async', lambda settings: original(settings, persistence=shared))
    monkeypatch.setattr(worker, 'CheckInScheduler', lambda store: SimpleNamespace(plan_all=lambda: None))

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'deliver_due_once', cancel)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.checkin_forever(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn)))
        assert not shared.database.pool.closed
        assert shared.context.ping()
    finally:
        shared.close()
        drop()


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_companion_builder_model_failure_releases_owned_actual_pg_pool(monkeypatch, tmp_path):
    dsn, drop = _pg_dsn()
    created = []
    original = persistence.persistence_from_settings

    def capture(settings):
        value = original(settings)
        created.append(value)
        return value

    def broken_model(*args):
        raise RuntimeError('model configuration rejected')

    monkeypatch.setattr(persistence, 'persistence_from_settings', capture)
    monkeypatch.setattr(runtime, 'build_role_model', broken_model)
    try:
        with pytest.raises(RuntimeError, match='model configuration rejected'):
            runtime.build_companion(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn))
        assert created[0].database.pool.closed
    finally:
        for value in created:
            value.close()
        drop()

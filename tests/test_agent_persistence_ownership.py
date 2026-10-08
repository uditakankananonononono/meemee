"""Successful async composition transfers only constructed persistence ownership."""
import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee import runtime
from meemee.config import Settings
from meemee.persistence import build_persistence


@pytest.mark.skipif(not PG_DSN, reason='requires actual PostgreSQL')
@pytest.mark.parametrize('borrowed', [False, True])
async def test_agent_close_actual_pg_pool_ownership(monkeypatch, tmp_path, borrowed):
    dsn, drop = _pg_dsn()
    shared = build_persistence('postgresql', tmp_path, dsn) if borrowed else None
    created = []
    original = runtime.persistence_from_settings
    def capture(settings):
        value = original(settings)
        created.append(value)
        return value
    monkeypatch.setattr(runtime, 'persistence_from_settings', capture)
    agent = None
    try:
        agent = await runtime.build_agent_async(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn), persistence=shared)
        await agent.aclose()
        pool = shared.database.pool if borrowed else created[0].database.pool
        assert pool.closed is (not borrowed)
        if borrowed:
            assert shared.context.ping()
    finally:
        if agent is not None:
            await agent.aclose()
        for value in created + ([shared] if shared is not None else []):
            value.close()
        drop()


@pytest.mark.skipif(not PG_DSN, reason='requires actual PostgreSQL')
async def test_owned_pg_pool_closes_even_when_model_close_raises(monkeypatch, tmp_path):
    dsn, drop = _pg_dsn()
    created = []
    original = runtime.persistence_from_settings
    def capture(settings):
        value = original(settings)
        created.append(value)
        return value
    monkeypatch.setattr(runtime, 'persistence_from_settings', capture)
    agent = None
    try:
        agent = await runtime.build_agent_async(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn))
        original_close = agent.model.aclose
        async def fail():
            await original_close()
            raise RuntimeError('model close failed')
        monkeypatch.setattr(agent.model, 'aclose', fail)
        with pytest.raises(RuntimeError, match='model close failed'):
            await agent.aclose()
        assert created[0].database.pool.closed
    finally:
        if agent is not None:
            await agent.model.client.aclose()
            await agent.tools.aclose()
        for value in created:
            value.close()
        drop()

"""Interactive chat keeps and releases its owned composition on every exit."""
import asyncio

import httpx
import pytest

from meemee.companion import cli
from meemee.config import Settings


@pytest.mark.parametrize('failure', ['empty', 'reply', 'output'])
def test_chat_closes_actual_owned_model_and_channels(monkeypatch, tmp_path, failure):
    released, made = [], []
    original = cli.build_companion_async
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, name):
            self.name = name
        async def aclose(self):
            released.append(self.name)
    async def build(*args):
        companion = await original(Settings(_env_file=None, data_dir=tmp_path))
        made.append(companion)
        for name, client in [('model', companion.model.client)] + [(n, c.client) for n, c in companion.channels.items() if hasattr(c, 'client')]:
            await client._transport.aclose()
            client._transport = Transport(name)
        async def reply(*args):
            if failure == 'reply':
                raise TypeError('reply failed')
            from types import SimpleNamespace
            return SimpleNamespace(persona='test', reply='test')
        companion.engine.reply = reply
        return companion
    monkeypatch.setattr(cli, 'build_companion_async', build)
    monkeypatch.setattr(cli.typer, 'prompt', lambda *args, **kwargs: '' if failure == 'empty' else 'test')
    def echo(*args):
        raise ValueError('output failed')
    monkeypatch.setattr(cli.typer, 'echo', echo)
    try:
        if failure == 'empty':
            cli.chat('owner')
        else:
            with pytest.raises(TypeError if failure == 'reply' else ValueError):
                cli.chat('owner')
        assert released == ['imessage', 'whatsapp', 'webhook', 'model']
    finally:
        for companion in made:
            async def close(companion=companion):
                await companion.model.aclose()
                for channel in companion.channels.values():
                    if hasattr(channel, 'aclose'):
                        await channel.aclose()
            asyncio.run(close())


@pytest.mark.parametrize('borrowed', [False, True])
def test_chat_actual_pg_ownership(monkeypatch, tmp_path, borrowed):
    from test_token_audit_backends import PG_DSN, _pg_dsn
    if not PG_DSN:
        pytest.skip('requires actual PostgreSQL')
    from meemee.persistence import build_persistence
    dsn, drop = _pg_dsn()
    shared = build_persistence('postgresql', tmp_path, dsn) if borrowed else None
    original = cli.build_companion_async
    made = []
    async def build(*args):
        companion = await original(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn), persistence=shared)
        made.append(companion)
        return companion
    monkeypatch.setattr(cli, 'build_companion_async', build)
    monkeypatch.setattr(cli.typer, 'prompt', lambda *args, **kwargs: '')
    try:
        cli.chat('owner')
        if borrowed:
            assert made[0].owned_persistence is None
            assert not shared.database.pool.closed
            assert shared.context.ping()
        else:
            assert made[0].owned_persistence.database.pool.closed
    finally:
        if shared is not None:
            shared.close()
        for companion in made:
            if companion.owned_persistence is not None:
                companion.owned_persistence.close()
        drop()

"""Noninteractive companion commands release their owned HTTP composition."""
import asyncio

import httpx
import pytest
from typer.testing import CliRunner

from meemee.cli import app
from meemee.companion import cli
from meemee.companion.models import UserProfile
from meemee.config import Settings

COMMANDS = [
    ['upsert-user', 'owner', '--display-name', 'Owner'],
    ['show-user', 'owner'],
    ['set-persona', 'owner', '--tone', 'direct'],
    ['set-checkins', 'owner', '--enabled'],
    ['add-fact', 'owner', 'test fact'],
    ['facts', 'owner'],
]


@pytest.mark.parametrize('command', COMMANDS)
@pytest.mark.parametrize('output_fails', [False, True])
def test_commands_release_actual_transports(monkeypatch, tmp_path, command, output_fails):
    released, made = [], []
    original = cli.build_companion_sync
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, name):
            self.name = name
        async def aclose(self):
            released.append(self.name)
    def build(*args):
        value = original(Settings(_env_file=None, data_dir=tmp_path))
        made.append(value)
        value.store.upsert_user(UserProfile(user_id='owner', display_name='Owner', timezone='UTC'))
        for name, client in [('model', value.model.client)] + [(n, c.client) for n, c in value.channels.items() if hasattr(c, 'client')]:
            client._transport = Transport(name)
        return value
    monkeypatch.setattr(cli, 'build_companion_sync', build)
    if output_fails:
        def echo(*args, **kwargs):
            raise ValueError('output failed')
        monkeypatch.setattr(cli.typer, 'echo', echo)
    try:
        result = CliRunner().invoke(app, ['companion'] + command)
        assert result.exit_code == (1 if output_fails else 0), result.output
        assert released == ['imessage', 'whatsapp', 'webhook', 'model']
    finally:
        for value in made:
            async def close(value=value):
                await value.model.aclose()
                for channel in value.channels.values():
                    if hasattr(channel, 'aclose'):
                        await channel.aclose()
            asyncio.run(close())


@pytest.mark.parametrize('borrowed', [False, True])
def test_commands_actual_pg_ownership(monkeypatch, tmp_path, borrowed):
    from test_token_audit_backends import PG_DSN, _pg_dsn
    if not PG_DSN:
        pytest.skip('requires actual PostgreSQL')
    from meemee.companion.runtime import build_companion_sync
    from meemee.persistence import build_persistence
    dsn, drop = _pg_dsn()
    shared = build_persistence('postgresql', tmp_path, dsn) if borrowed else None
    made = []
    def build(*args):
        value = build_companion_sync(Settings(_env_file=None, data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=dsn), persistence=shared)
        made.append(value)
        return value
    monkeypatch.setattr(cli, 'build_companion_sync', build)
    try:
        result = CliRunner().invoke(app, ['companion', 'show-user', 'unknown'])
        assert result.exit_code != 0
        if borrowed:
            assert not shared.database.pool.closed
            assert shared.context.ping()
        else:
            assert made[0].owned_persistence.database.pool.closed
        assert made[0].model.client.is_closed
    finally:
        if shared is not None:
            shared.close()
        for value in made:
            if value.owned_persistence is not None:
                value.owned_persistence.close()
        drop()

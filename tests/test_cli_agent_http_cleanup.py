"""Agent CLI owns its model/tools until output succeeds or execution fails."""
import asyncio

import httpx
import pytest

from meemee import cli
from meemee.config import Settings
from meemee.memory import MemoryStore
from meemee.runtime import build_agent_async
from meemee.types import RunReport


@pytest.mark.parametrize('failure', ['none', 'run', 'output'])
def test_cli_run_releases_owned_actual_http_transports(monkeypatch, tmp_path, failure):
    released, made = [], []
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, name):
            self.name = name
        async def aclose(self):
            released.append(self.name)
    async def build():
        agent = await build_agent_async(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
        made.append(agent)
        for name, client in [('model', agent.model.client)] + [(n, agent.tools.get(n).client) for n in
                ['github.search_repositories', 'github.push_branch', 'github.create_pull_request']]:
            await client._transport.aclose()
            client._transport = Transport(name)
        async def run(goal, **kwargs):
            if failure == 'run':
                raise TypeError('run failed')
            assert kwargs['owner_id'] == 'default'
            return RunReport(run_id='run', goal=goal, final='done', steps_used=0, tool_results=[])
        agent.run = run
        return agent
    def echo(*args, **kwargs):
        if failure == 'output':
            raise ValueError('output failed')
    monkeypatch.setattr(cli, 'build_agent_async', build)
    monkeypatch.setattr(cli.typer, 'echo', echo)
    try:
        if failure == 'none':
            cli.run('goal', approve_writes=False)
        else:
            with pytest.raises(TypeError if failure == 'run' else ValueError, match=f'{failure} failed'):
                cli.run('goal', approve_writes=False)
        assert released == ['github.create_pull_request', 'github.push_branch', 'github.search_repositories', 'model']
    finally:
        for agent in made:
            asyncio.run(agent.aclose())

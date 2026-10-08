"""API shutdown releases owned clients and still attempts remaining cleanup."""
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from meemee.config import Settings
from meemee.llm import OpenAICompatibleModel
from meemee.memory import MemoryStore
from meemee.runtime import build_agent_async
from meemee.shutdown import RunGate


@pytest.mark.parametrize('resource', ['tools', 'companion', 'reflection'])
async def test_lifespan_releases_actual_owned_http_clients(monkeypatch, tmp_path, resource):
    from meemee import api

    agent = await build_agent_async(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
    companion_model = OpenAICompatibleModel('http://unused', 'model', 'key')
    reflection = OpenAICompatibleModel('http://unused', 'model', 'key')
    clients = {
        'tools': [agent.tools.get(name).client for name in ['github.search_repositories', 'github.push_branch', 'github.create_pull_request']],
        'companion': [companion_model.client],
        'reflection': [reflection.client],
    }
    released = []
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, label):
            self.label = label
        async def aclose(self):
            released.append(self.label)
    for label, group in clients.items():
        for index, client in enumerate(group):
            await client._transport.aclose()
            client._transport = Transport((label, index))
    objects = {'agent': agent}
    objects['companion'] = SimpleNamespace(model=companion_model)
    objects['reflection_model'] = reflection
    objects['run_gate'] = RunGate()
    objects['browser_sessions'] = SimpleNamespace(shutdown=Mock())
    objects['persistence'] = SimpleNamespace(close=Mock())
    install_bootstrap(monkeypatch, api, objects)
    try:
        async with api.lifespan(api.app):
            pass
        assert all(client.is_closed for client in clients[resource])
        assert all((resource, index) in released for index in range(len(clients[resource])))
    finally:
        await agent.aclose()
        await companion_model.aclose()
        await reflection.aclose()


async def test_shutdown_attempts_remaining_cleanup_after_close_error(monkeypatch):
    from meemee import api

    events = []
    class Model:
        async def aclose(self):
            events.append('other')
    class FailingModel:
        async def aclose(self):
            events.append('agent-model')
            raise RuntimeError('close failed')
    class Agent:
        model = FailingModel()
        async def aclose(self):
            events.append('agent')
            raise RuntimeError('close failed')
    objects = {'agent': Agent()}
    objects['companion'] = SimpleNamespace(model=Model())
    objects['reflection_model'] = Model()
    objects['run_gate'] = RunGate()
    objects['browser_sessions'] = SimpleNamespace(shutdown=lambda: events.append('browser'))
    objects['persistence'] = SimpleNamespace(close=lambda: events.append('persistence'))
    install_bootstrap(monkeypatch, api, objects)
    with pytest.raises(RuntimeError, match='close failed'):
        async with api.lifespan(api.app):
            pass
    assert events == ['agent', 'other', 'other', 'browser', 'persistence']




def install_bootstrap(monkeypatch, api, objects):
    async def bootstrap(cleanup):
        cleanup.callback(objects['persistence'].close)
        cleanup.callback(objects['browser_sessions'].shutdown)
        seen = {id(objects['agent'].model)}
        for model in (objects['reflection_model'], objects['companion'].model):
            if id(model) not in seen:
                seen.add(id(model))
                cleanup.push_async_callback(model.aclose)
        cleanup.push_async_callback(objects['agent'].aclose)
        return objects
    monkeypatch.setattr(api, 'bootstrap_api', bootstrap)

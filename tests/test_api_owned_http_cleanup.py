"""API shutdown releases owned clients and still attempts remaining cleanup."""
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from meemee.config import Settings
from meemee.llm import OpenAICompatibleModel
from meemee.memory import MemoryStore
from meemee.runtime import build_agent
from meemee.shutdown import RunGate


@pytest.mark.parametrize('resource', ['tools', 'companion', 'reflection'])
async def test_lifespan_releases_actual_owned_http_clients(monkeypatch, tmp_path, resource):
    from meemee import api

    agent = build_agent(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
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
    monkeypatch.setattr(api, 'agent', agent)
    monkeypatch.setattr(api, 'companion', SimpleNamespace(model=companion_model))
    monkeypatch.setattr(api, 'reflection_model', reflection)
    monkeypatch.setattr(api, 'run_gate', RunGate())
    monkeypatch.setattr(api, 'browser_sessions', SimpleNamespace(shutdown=Mock()))
    monkeypatch.setattr(api, 'persistence', SimpleNamespace(close=Mock()))
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
    monkeypatch.setattr(api, 'agent', Agent())
    monkeypatch.setattr(api, 'companion', SimpleNamespace(model=Model()))
    monkeypatch.setattr(api, 'reflection_model', Model())
    monkeypatch.setattr(api, 'run_gate', RunGate())
    monkeypatch.setattr(api, 'browser_sessions', SimpleNamespace(shutdown=lambda: events.append('browser')))
    monkeypatch.setattr(api, 'persistence', SimpleNamespace(close=lambda: events.append('persistence')))
    with pytest.raises(RuntimeError, match='close failed'):
        async with api.lifespan(api.app):
            pass
    assert events == ['agent', 'other', 'other', 'browser', 'persistence']


async def test_shutdown_closes_shared_model_once_on_body_error(monkeypatch):
    from meemee import api

    events = []
    class Model:
        async def aclose(self):
            events.append('model')
    shared = Model()
    class Agent:
        model = shared
        async def aclose(self):
            await shared.aclose()
    monkeypatch.setattr(api, 'agent', Agent())
    monkeypatch.setattr(api, 'companion', SimpleNamespace(model=shared))
    monkeypatch.setattr(api, 'reflection_model', shared)
    monkeypatch.setattr(api, 'run_gate', RunGate())
    monkeypatch.setattr(api, 'browser_sessions', SimpleNamespace(shutdown=lambda: events.append('browser')))
    monkeypatch.setattr(api, 'persistence', SimpleNamespace(close=lambda: events.append('persistence')))
    with pytest.raises(ValueError, match='body failed'):
        async with api.lifespan(api.app):
            raise ValueError('body failed')
    assert events == ['model', 'browser', 'persistence']

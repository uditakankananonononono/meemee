"""Retained unresolved synchronous-factory HTTP ownership failure."""
import pytest

from meemee import runtime
from meemee.config import Settings
from meemee.memory import MemoryStore


async def test_runtime_model_failure_releases_constructed_tool_clients(monkeypatch, tmp_path):
    tools = []
    original = runtime.GitHubRepoSearch
    def capture(*args, **kwargs):
        tool = original(*args, **kwargs)
        tools.append(tool)
        return tool
    def fail(*args, **kwargs):
        raise RuntimeError('construction failed')
    monkeypatch.setattr(runtime, 'GitHubRepoSearch', capture)
    monkeypatch.setattr(runtime, 'build_role_model', fail)
    try:
        with pytest.raises(RuntimeError, match='construction failed'):
            await runtime.build_agent_async(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
        assert tools[0].client.is_closed
    finally:
        for tool in tools:
            await tool.aclose()


async def test_sync_factory_rejects_running_loop_before_acquiring(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('resource acquired')
    monkeypatch.setattr(runtime, 'GitHubRepoSearch', forbidden)
    with pytest.raises(RuntimeError, match='await the async factory'):
        runtime.build_agent_sync()


async def test_agent_failure_releases_model_and_tools_in_reverse_order(monkeypatch, tmp_path):
    import httpx

    from meemee.llm import OpenAICompatibleModel
    events = []
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, name):
            self.name = name
        async def aclose(self):
            events.append(self.name)
    model = OpenAICompatibleModel('http://unused', 'model', 'key')
    await model.client._transport.aclose()
    model.client._transport = Transport('model')
    monkeypatch.setattr(runtime, 'build_role_model', lambda *args: model)
    originals = {name: getattr(runtime, name) for name in ['GitHubRepoSearch', 'GitHubPushBranch', 'GitHubCreatePullRequest']}
    made = []
    for name, original in originals.items():
        def capture(*args, _original=original, _name=name):
            tool = _original(*args)
            # Unused default transports have acquired no connections.
            tool.client._transport = Transport(_name)
            made.append(tool)
            return tool
        monkeypatch.setattr(runtime, name, capture)
    def fail(*args):
        raise RuntimeError('policy rejected')
    monkeypatch.setattr(runtime.PolicyEngine, 'from_file', fail)
    try:
        with pytest.raises(RuntimeError, match='policy rejected'):
            await runtime.build_agent_async(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
        assert events == ['model', 'GitHubCreatePullRequest', 'GitHubPushBranch', 'GitHubRepoSearch']
        assert model.client.is_closed and all(t.client.is_closed for t in made)
    finally:
        await model.aclose()
        for tool in made:
            await tool.aclose()


async def test_companion_channel_failure_releases_actual_model(monkeypatch, tmp_path):
    import httpx

    from meemee.companion import runtime as companion_runtime
    from meemee.llm import OpenAICompatibleModel
    released = []
    class Transport(httpx.AsyncBaseTransport):
        async def aclose(self):
            released.append('model')
    model = OpenAICompatibleModel('http://unused', 'model', 'key')
    await model.client._transport.aclose()
    model.client._transport = Transport()
    monkeypatch.setattr(companion_runtime, 'build_role_model', lambda *args: model)
    async def fail(*args):
        raise RuntimeError('channel rejected')
    monkeypatch.setattr(companion_runtime, 'build_channels_async', fail)
    try:
        with pytest.raises(RuntimeError, match='channel rejected'):
            await companion_runtime.build_companion_async(Settings(_env_file=None, data_dir=tmp_path))
        assert released == ['model']
    finally:
        await model.aclose()


async def test_channel_partial_build_releases_earlier_adapter(monkeypatch, tmp_path):
    from meemee.companion import channels
    released = []
    original = channels.WebhookChannel
    made = []
    def capture(*args, **kwargs):
        channel = original(*args, **kwargs)
        import httpx
        class Transport(httpx.AsyncBaseTransport):
            async def aclose(self):
                released.append('webhook')
        channel.client._transport = Transport()
        made.append(channel)
        return channel
    def fail(*args, **kwargs):
        raise RuntimeError('provider rejected')
    monkeypatch.setattr(channels, 'WebhookChannel', capture)
    monkeypatch.setattr(channels, 'ProviderChannel', fail)
    try:
        with pytest.raises(RuntimeError, match='provider rejected'):
            await channels.build_channels_async(Settings(_env_file=None, data_dir=tmp_path), object())
        assert made[0].client.is_closed
        assert released == ['webhook']
    finally:
        for channel in made:
            await channel.aclose()
    assert released == ['webhook']


def test_sync_factory_completes_without_running_loop(tmp_path):
    import asyncio
    agent = runtime.build_agent_sync(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
    try:
        assert not agent.model.client.is_closed
    finally:
        asyncio.run(agent.aclose())

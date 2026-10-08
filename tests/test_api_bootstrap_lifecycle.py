"""Awaited startup is the only API resource acquisition path."""

import pytest

from meemee import api
from meemee.api_state import APINotStartedError


async def test_resources_require_successful_startup():
    with pytest.raises(APINotStartedError):
        _ = api.agent.model
    async with api.lifespan(api.app):
        assert api.agent.model is api.reflection_model.value()
    with pytest.raises(APINotStartedError):
        _ = api.companion.engine


async def test_import_in_running_loop_does_not_acquire_resources():
    import asyncio
    import os
    import sys
    import tempfile
    code = '''
import asyncio
async def main():
 from meemee import persistence, runtime
 def forbidden(*a, **kw):raise AssertionError('acquired during import')
 persistence.persistence_from_settings=forbidden
 runtime.build_agent=forbidden
 runtime.build_agent_async=forbidden
 from meemee import api
 assert not api._resources
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
        env={**os.environ, 'MEEMEE_DATA_DIR': tempfile.mkdtemp()},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate()
    assert process.returncode == 0, (out + err).decode()


async def test_failed_bootstrap_unwinds_model_tools_and_persistence(monkeypatch, tmp_path):
    import httpx

    from meemee.config import Settings
    events = []
    created = []
    original = api.build_agent_async
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, name):
            self.name = name
        async def aclose(self):
            events.append(self.name)
    async def capture(*args, **kwargs):
        child = await original(*args, **kwargs)
        created.append(child)
        for name, client in [('model', child.model.client)] + [(name, child.tools.get(name).client) for name in
                ['github.search_repositories', 'github.push_branch', 'github.create_pull_request']]:
            await client._transport.aclose()
            client._transport = Transport(name)
        return child
    async def fail(*args, **kwargs):
        raise RuntimeError('companion rejected')
    monkeypatch.setattr(api, 'settings', Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(api, 'build_agent_async', capture)
    monkeypatch.setattr(api, 'build_companion_async', fail)
    with pytest.raises(RuntimeError, match='companion rejected'):
        async with api.lifespan(api.app):
            pytest.fail('startup yielded')
    assert events == ['github.create_pull_request', 'github.push_branch', 'github.search_repositories', 'model']
    assert not api._resources
    assert created[0].model.client.is_closed


async def test_startup_does_not_publish_partial_state(monkeypatch):
    async def fail(cleanup):
        cleanup.callback(lambda: events.append('closed'))
        raise ValueError('startup rejected')
    events = []
    monkeypatch.setattr(api, 'bootstrap_api', fail)
    with pytest.raises(ValueError, match='startup rejected'):
        async with api.lifespan(api.app):
            pass
    assert events == ['closed'] and not api._resources


async def test_failed_late_bootstrap_releases_companion_and_reflection(monkeypatch, tmp_path):
    from meemee.config import Settings
    made = []
    original_agent = api.build_agent_async
    original_companion = api.build_companion_async
    async def agent(*args, **kwargs):
        value = await original_agent(*args, **kwargs)
        made.append(value.model)
        return value
    async def companion(*args, **kwargs):
        value = await original_companion(*args, **kwargs)
        made.append(value.model)
        made.extend(channel for channel in value.channels.values() if hasattr(channel, 'client'))
        return value
    def fail(*args):
        raise RuntimeError('auth rejected')
    monkeypatch.setattr(api, 'settings', Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(api, 'build_agent_async', agent)
    monkeypatch.setattr(api, 'build_companion_async', companion)
    monkeypatch.setattr(api, 'Authenticator', fail)
    with pytest.raises(RuntimeError, match='auth rejected'):
        async with api.lifespan(api.app):
            pass
    assert all(value.client.is_closed for value in made)
    assert not api._resources


async def test_post_bootstrap_setup_failure_clears_state_and_allows_retry(monkeypatch):
    from meemee.shutdown import RunGate
    events = []
    class Browser:
        def __setattr__(self, name, value):
            if name == 'notice_delivery':
                raise RuntimeError('notice setter rejected')
    async def bootstrap(cleanup):
        cleanup.callback(lambda: events.append('closed'))
        return {'browser_sessions': Browser(), 'run_gate': RunGate()}
    monkeypatch.setattr(api, 'bootstrap_api', bootstrap)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='notice setter rejected'):
                async with api.lifespan(api.app):
                    pass
            assert not api._resources
        assert events == ['closed', 'closed']
    finally:
        api._resources.clear()


async def test_concurrent_startup_rejects_second_before_acquisition(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from meemee.shutdown import RunGate
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def bootstrap(cleanup):
        calls.append(True)
        entered.set()
        await release.wait()
        return {'browser_sessions': SimpleNamespace(), 'run_gate': RunGate()}
    monkeypatch.setattr(api, 'bootstrap_api', bootstrap)
    async def first():
        async with api.lifespan(api.app):
            pass
    task = asyncio.create_task(first())
    await entered.wait()
    try:
        async def second():
            async with api.lifespan(api.app):
                pass
        with pytest.raises(RuntimeError, match='already starting or started'):
            await asyncio.wait_for(second(), .1)
    finally:
        release.set()
        await task
    assert calls == [True] and not api._resources


async def test_production_bootstrap_deduplicates_shared_owned_models(monkeypatch, tmp_path):
    from meemee.config import Settings
    original_agent, original_companion = api.build_agent_async, api.build_companion_async
    made, closed = [], []
    async def agent(*args, **kwargs):
        value = await original_agent(*args, **kwargs)
        made.append(value)
        close = value.model.aclose
        async def counted():
            closed.append('model')
            await close()
        value.model.aclose = counted
        return value
    async def companion(*args, **kwargs):
        value = await original_companion(*args, **kwargs)
        await value.model.aclose()
        value.model = made[0].model
        value.engine.model = value.model
        return value
    monkeypatch.setattr(api, 'settings', Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(api, 'build_agent_async', agent)
    monkeypatch.setattr(api, 'build_companion_async', companion)
    async with api.lifespan(api.app):
        assert api.agent.model is api.companion.model
    assert closed == ['model']

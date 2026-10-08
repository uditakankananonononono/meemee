"""Per-job worker agents release actual constructed GitHub tool clients."""

import asyncio

import pytest

from meemee import worker
from meemee.config import Settings
from meemee.persistence import build_persistence
from meemee.types import RunReport


@pytest.mark.parametrize('cancelled', [False, True])
async def test_worker_releases_actual_per_job_tool_clients(monkeypatch, tmp_path, cancelled):
    stores = build_persistence('sqlite', tmp_path, vault_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
    stores.jobs.enqueue('work', principal='owner')
    original_claim = stores.jobs.claim
    claimed = []

    class EndLoop(Exception):
        pass

    def claim():
        if claimed:
            raise EndLoop
        claimed.append(True)
        return original_claim()

    monkeypatch.setattr(stores.jobs, 'claim', claim)
    monkeypatch.setattr(worker, 'persistence_from_settings', lambda settings: stores)
    agents = []
    original = worker.build_agent

    def capture(*args, **kwargs):
        agent = original(*args, **kwargs)
        agents.append(agent)

        async def run(goal, **kw):
            if cancelled:
                raise asyncio.CancelledError
            return RunReport(run_id=kw['run_id'], goal=goal, final='done', steps_used=0, tool_results=[])

        agent.run = run
        return agent

    monkeypatch.setattr(worker, 'build_agent', capture)
    try:
        with pytest.raises(asyncio.CancelledError if cancelled else EndLoop):
            await worker.work_forever(Settings(_env_file=None, data_dir=tmp_path))
        assert all(agents[0].tools.get(name).client.is_closed for name in
                   ['github.search_repositories', 'github.push_branch', 'github.create_pull_request'])
    finally:
        for agent in agents:
            await agent.model.aclose()
            for name in ['github.search_repositories', 'github.push_branch', 'github.create_pull_request']:
                await agent.tools.get(name).client.aclose()


async def test_github_tool_borrowed_actual_clients_stay_open():
    import httpx

    from meemee.tools.github import GitHubPushBranch, GitHubRepoSearch

    client = httpx.AsyncClient()
    try:
        for tool in [GitHubRepoSearch(client=client), GitHubPushBranch(None, client=client)]:
            await tool.aclose()
        assert not client.is_closed
    finally:
        await client.aclose()

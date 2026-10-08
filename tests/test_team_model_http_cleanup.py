"""Factory-created team children release their real model HTTP client."""

import asyncio

import pytest

from meemee.config import Settings
from meemee.persistence import build_persistence
from meemee.runtime import build_agent
from meemee.team import AgentTeam
from meemee.types import RunReport


@pytest.mark.parametrize('outcome', ['success', 'typeerror', 'cancelled'])
async def test_team_child_actual_model_client_closes(monkeypatch, tmp_path, outcome):
    stores = build_persistence('sqlite', tmp_path)
    children = []

    def factory():
        child = build_agent(Settings(_env_file=None, data_dir=tmp_path), False, persistence=stores)
        children.append(child)

        async def run(goal, *, owner_id, cancel=None):
            if outcome == 'typeerror':
                raise TypeError('effect happened')
            if outcome == 'cancelled':
                raise asyncio.CancelledError
            return RunReport(run_id='run', goal=goal, final='done', steps_used=0, tool_results=[])

        child.run = run
        return child

    try:
        if outcome == 'success':
            assert (await AgentTeam(factory).delegate(['work'], owner_id='owner'))[0]['ok']
        else:
            with pytest.raises(TypeError if outcome == 'typeerror' else asyncio.CancelledError):
                await AgentTeam(factory).delegate(['work'], owner_id='owner')
        assert len(children) == 1
        assert children[0].model.client.is_closed
    finally:
        for child in children:
            await child.model.aclose()


async def test_team_owned_actual_tool_clients_close(tmp_path):
    stores = build_persistence('sqlite', tmp_path)
    children = []

    def factory():
        child = build_agent(Settings(_env_file=None, data_dir=tmp_path), False, persistence=stores)
        children.append(child)

        async def run(goal, *, owner_id):
            return RunReport(run_id='run', goal=goal, final='done', steps_used=0, tool_results=[])

        child.run = run
        return child

    try:
        await AgentTeam(factory).delegate(['work'], owner_id='owner')
        assert all(children[0].tools.get(name).client.is_closed for name in
                   ['github.search_repositories', 'github.push_branch', 'github.create_pull_request'])
    finally:
        for child in children:
            await child.aclose()

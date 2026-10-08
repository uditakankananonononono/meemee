"""A propagated child error must not leave sibling work running after delegate exits."""

import asyncio

import pytest

from meemee.config import Settings
from meemee.persistence import build_persistence
from meemee.runtime import build_agent
from meemee.team import AgentTeam


async def test_team_error_joins_sibling_and_closes_actual_model(tmp_path):
    stores = build_persistence('sqlite', tmp_path)
    started = asyncio.Event()
    stopped = asyncio.Event()
    children = []

    def factory():
        child = build_agent(Settings(_env_file=None, data_dir=tmp_path), False, persistence=stores)
        children.append(child)

        async def run(goal, *, owner_id):
            if goal == 'bad':
                await started.wait()
                raise TypeError('internal failure after effect')
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        child.run = run
        return child

    before = asyncio.all_tasks()
    try:
        with pytest.raises(TypeError, match='internal failure after effect'):
            await AgentTeam(factory).delegate(['bad', 'slow'], owner_id='owner')
        assert stopped.is_set()
        assert all(child.model.client.is_closed for child in children)
        assert not (asyncio.all_tasks() - before)
    finally:
        for task in asyncio.all_tasks() - before:
            task.cancel()
        await asyncio.gather(*(asyncio.all_tasks() - before), return_exceptions=True)
        for child in children:
            await child.model.aclose()

"""Per-job worker agents release their actual HTTP model client."""

import asyncio

import pytest

from meemee import worker
from meemee.config import Settings
from meemee.persistence import build_persistence
from meemee.types import RunReport


@pytest.mark.parametrize('cancelled', [False, True])
async def test_worker_releases_actual_per_job_model(monkeypatch, tmp_path, cancelled):
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
        assert agents[0].model.client.is_closed
    finally:
        for agent in agents:
            await agent.model.aclose()

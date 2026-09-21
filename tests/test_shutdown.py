import asyncio

import pytest

from meemee.shutdown import RunGate


@pytest.mark.asyncio
async def test_drain_waits_for_active_run():
    gate = RunGate(); await gate.enter()
    drain = asyncio.create_task(gate.drain(1))
    await asyncio.sleep(0)
    assert not gate.accepting and not drain.done()
    await gate.leave()
    assert await drain


@pytest.mark.asyncio
async def test_drain_times_out_and_rejects_new_runs():
    gate = RunGate(); await gate.enter()
    assert not await gate.drain(0.001)
    with pytest.raises(RuntimeError, match="draining"):
        await gate.enter()
    await gate.leave()


@pytest.mark.asyncio
async def test_lifespan_shutdown_drains_and_closes(monkeypatch):
    from meemee import api

    closed = False
    async def close():
        nonlocal closed
        closed = True
    monkeypatch.setattr(api.agent.model, "aclose", close)
    async with api.lifespan(api.app):
        assert api.run_gate.accepting
    assert closed

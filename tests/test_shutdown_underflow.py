"""Rejected unbalanced leaves must not corrupt the graceful-drain counter."""

import pytest

from meemee.shutdown import RunGate


@pytest.mark.asyncio
async def test_unbalanced_leave_preserves_gate_and_drain():
    gate = RunGate()
    with pytest.raises(RuntimeError, match='underflow'):
        await gate.leave()
    assert gate.active == 0
    await gate.enter()
    assert gate.active == 1
    await gate.leave()
    assert gate.active == 0
    assert await gate.drain(0.01) is True

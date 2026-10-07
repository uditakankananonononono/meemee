"""A failed delegated child must not discard sibling results or replay effects."""
from types import SimpleNamespace

import pytest

from meemee.team import AgentTeam


@pytest.mark.asyncio
async def test_internal_child_typeerror_propagates_without_replay():
    calls = []
    class Child:
        async def run(self, goal, *, owner_id):
            calls.append(goal)
            if goal == 'bad':
                raise TypeError('internal failure after effect')
            return SimpleNamespace(model_dump=lambda:{'final':'good'})
    with pytest.raises(TypeError, match='internal failure after effect'):
        await AgentTeam(Child).delegate(['bad','good'], owner_id='owner')
    assert calls.count('bad') == calls.count('good') == 1

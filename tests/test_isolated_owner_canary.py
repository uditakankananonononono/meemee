"""Process isolation must not silently drop the caller's tenant identity."""
import pytest
from pydantic import BaseModel

from meemee.isolation import IsolationPolicy
from meemee.tools.base import Tool, ToolRegistry


class Args(BaseModel):
    pass


class OwnerTool(Tool):
    name = 'owner'
    description = 'owner identity canary'
    arguments_model = Args
    isolation = IsolationPolicy(timeout_s=5, grace_s=0.1)
    async def run(self, arguments, *, owner_id='default'):
        return {'owner_id': owner_id}


@pytest.mark.asyncio
async def test_isolated_tool_receives_authenticated_owner():
    registry = ToolRegistry()
    registry.register(OwnerTool())
    result = await registry.execute('owner', {}, owner_id='tenant-alice')
    assert result.ok
    assert result.content == {'owner_id': 'tenant-alice'}

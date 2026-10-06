import pytest
from pydantic import BaseModel

from meemee.tools.base import Tool, ToolRegistry


class Arguments(BaseModel):
    pass


class SideEffectThenTypeError(Tool):
    name = "test.side_effect"
    description = "test no replay after a partial failure"
    arguments_model = Arguments
    def __init__(self): self.calls = 0
    def run(self, arguments):
        self.calls += 1
        raise TypeError("internal failure after side effect")


@pytest.mark.asyncio
async def test_synchronous_tool_internal_typeerror_never_replays_action():
    tool = SideEffectThenTypeError()
    registry = ToolRegistry()
    registry.register(tool)
    # An internal TypeError after a side effect must not crash the whole run and
    # must not replay the action: the tool result records the failure once.
    result = await registry.execute(tool.name, {}, owner_id="default")
    assert not result.ok and "internal failure" in result.error
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_synchronous_tool_valueerror_never_replays_action():
    class ValueErrorTool(SideEffectThenTypeError):
        def run(self, arguments):
            self.calls += 1
            raise ValueError("validation failed after side effect")
    tool = ValueErrorTool()
    registry = ToolRegistry()
    registry.register(tool)
    result = await registry.execute(tool.name, {}, owner_id="default")
    assert not result.ok and "validation failed" in result.error
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_team_does_not_replay_child_when_internal_typeerror_occurs():
    from meemee.team import AgentTeam
    class Child:
        def __init__(self): self.calls = 0
        async def run(self, goal, cancel=None, owner_id="default"):
            self.calls += 1
            raise TypeError("child failed after write")
    child = Child()
    with pytest.raises(TypeError, match="child failed"):
        await AgentTeam(lambda: child).delegate(["goal"], owner_id="alice")
    assert child.calls == 1

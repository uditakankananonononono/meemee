"""Retained unresolved synchronous-factory HTTP ownership failure."""
import pytest

from meemee import runtime
from meemee.config import Settings
from meemee.memory import MemoryStore


async def test_runtime_model_failure_releases_constructed_tool_clients(monkeypatch, tmp_path):
    tools = []
    original = runtime.GitHubRepoSearch
    def capture(*args, **kwargs):
        tool = original(*args, **kwargs)
        tools.append(tool)
        return tool
    def fail(*args, **kwargs):
        raise RuntimeError('construction failed')
    monkeypatch.setattr(runtime, 'GitHubRepoSearch', capture)
    monkeypatch.setattr(runtime, 'build_role_model', fail)
    try:
        with pytest.raises(RuntimeError, match='construction failed'):
            runtime.build_agent(Settings(_env_file=None, data_dir=tmp_path), memory=MemoryStore(tmp_path / 'memory.db'))
        assert tools[0].client.is_closed
    finally:
        for tool in tools:
            await tool.aclose()

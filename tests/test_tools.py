from pathlib import Path

import httpx
import pytest

from meemee.tools.base import ToolRegistry
from meemee.tools.filesystem import ReadFile, WriteFile
from meemee.tools.github import GitHubRepoSearch, GitHubSearchArgs


@pytest.mark.asyncio
async def test_workspace_round_trip(tmp_path: Path):
    writer, reader = WriteFile(tmp_path), ReadFile(tmp_path)
    await writer.run(writer.arguments_model(path="nested/x.txt", content="hello"))
    assert (await reader.run(reader.arguments_model(path="nested/x.txt")))["content"] == "hello"


@pytest.mark.asyncio
async def test_workspace_rejects_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        await ReadFile(tmp_path).run(ReadFile.arguments_model(path="../secret"))


@pytest.mark.asyncio
async def test_registry_validation(tmp_path: Path):
    registry = ToolRegistry(); registry.register(ReadFile(tmp_path))
    result = await registry.execute("workspace.read_file", {})
    assert not result.ok and "path" in result.error


def test_registry_duplicate(tmp_path: Path):
    registry = ToolRegistry(); registry.register(ReadFile(tmp_path))
    with pytest.raises(ValueError, match="already"):
        registry.register(ReadFile(tmp_path))


@pytest.mark.asyncio
async def test_github_search_and_ranking():
    def handler(request):
        return httpx.Response(200, json={"items": [
            {"full_name":"a/new","html_url":"https://github.com/a/new","description":"n","language":"Python","stargazers_count":100,"forks_count":20,"open_issues_count":2,"license":{"spdx_id":"MIT"},"updated_at":"2026-09-20T00:00:00Z","pushed_at":"2026-09-20T00:00:00Z","archived":False},
            {"full_name":"b/old","html_url":"https://github.com/b/old","description":"o","language":"Python","stargazers_count":10,"forks_count":1,"open_issues_count":20,"license":None,"updated_at":"2020-01-01T00:00:00Z","pushed_at":"2020-01-01T00:00:00Z","archived":False},
        ]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rows = await GitHubRepoSearch(client=client).run(GitHubSearchArgs(query="agents"))
    assert rows[0]["full_name"] == "a/new"
    assert rows[0]["quality_score"] > rows[1]["quality_score"]


@pytest.mark.asyncio
async def test_github_rate_limit_message():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        await GitHubRepoSearch(client=client).run(GitHubSearchArgs(query="agents"))

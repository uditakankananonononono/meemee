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
    result = await registry.execute("workspace.read_file", {}, owner_id="default")
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


def test_browser_session_open_tool_cannot_target_notices_and_owns_sessions():
    """Canary for the live tool-path IDOR: the tool schema carries no
    notify_user_id (a prompt-injected goal cannot aim a takeover link at a
    victim's companion inbox), and the session owner is the calling principal,
    never a shared "agent" bucket."""
    from meemee.tools.browser_session import BrowserSessionOpen, OpenArgs

    assert "notify_user_id" not in OpenArgs.model_fields

    calls = {}

    class FakeManager:
        async def open(self, url, owner_id, allowed_domains, profile, auto_takeover, notify_user_id=None):
            calls.update(owner_id=owner_id, notify_user_id=notify_user_id)
            return {"session_id": "bs_" + "0" * 32, "state": "open"}

    tool = BrowserSessionOpen(FakeManager())
    args = OpenArgs(url="https://example.com/")
    import asyncio
    asyncio.run(tool.run(args, owner_id="principal-p"))
    assert calls == {"owner_id": "principal-p", "notify_user_id": None}
    # Even if an injected goal smuggles the field in, it never reaches the manager.
    assert not hasattr(OpenArgs(url="https://example.com/", notify_user_id="victim"), "notify_user_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("name,extra", [
    ("browser.session_act", {"actions": [{"kind": "wait", "milliseconds": 1}]}),
    ("browser.session_snapshot", {}),
    ("browser.session_wait_human", {"timeout_seconds": 1}),
    ("browser.session_request_human", {"reason": "need help"}),
    ("browser.session_close", {}),
])
async def test_session_tools_check_owner_before_action(name, extra):
    from meemee.tools.browser_session import session_tools

    sid = "bs_" + "a" * 32
    calls = []

    class Store:
        def get_session(self, session_id):
            return {"owner_id": "victim"} if session_id == sid else None

    class Manager:
        store = Store()

        async def act(self, *args):
            calls.append(("act", args)); return {}

        async def snapshot(self, *args):
            calls.append(("snapshot", args)); return {}

        async def wait_for_human(self, *args):
            calls.append(("wait", args)); return {}

        async def request_takeover(self, *args):
            calls.append(("request", args)); return {"token": "secret"}

        async def close(self, *args):
            calls.append(("close", args)); return {}

    registry = ToolRegistry()
    for tool in session_tools(Manager()):
        registry.register(tool)
    denied = await registry.execute(name, {"session_id": sid, "owner_id": "victim", **extra}, owner_id="attacker")
    assert not denied.ok and denied.error == "unknown browser session"
    assert calls == []
    missing = await registry.execute(name, {"session_id": "bs_" + "0" * 32, **extra}, owner_id="victim")
    assert not missing.ok and missing.error == denied.error
    assert calls == []
    allowed = await registry.execute(name, {"session_id": sid, **extra}, owner_id="victim")
    assert allowed.ok and len(calls) == 1
    if name in {"browser.session_request_human", "browser.session_close"}:
        assert calls[0][1][-1] == "victim"
    assert "token" not in allowed.content

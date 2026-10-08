"""Network-class tool failures become failed ToolResults, not escaped exceptions (area 213)."""
import httpx
import pytest

from meemee.tools.base import ToolRegistry
from meemee.tools.github import GitHubCreatePullRequest, GitHubPushBranch, GitHubRepoSearch

OLD, NEW = "a" * 40, "b" * 40
TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
QUERY_CANARY = "canary" + "q" * 12
DEEP = b"[" * 1500 + b"]" * 1500

FAULTS = {
    "http500": lambda r: httpx.Response(500, request=r),
    "http404": lambda r: httpx.Response(404, request=r),
    "timeout": lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    "connect": lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)),
    "deep_json": lambda r: httpx.Response(200, content=DEEP, request=r),
}


def _registry(tool):
    registry = ToolRegistry()
    registry.register(tool)
    return registry


def _counting(fault):
    calls = []

    def handler(request):
        calls.append(request)
        return FAULTS[fault](request)

    return handler, calls


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SEARCH_ARGS = {"query": f"python agent {QUERY_CANARY}"}
PUSH_ARGS = {"owner": "o", "repository": "r", "branch": "main", "expected_head": OLD, "commit_sha": NEW}
PR_ARGS = {"owner": "o", "repository": "r", "title": "Ship it", "head": "f", "base": "main"}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(FAULTS))
async def test_search_network_faults_are_failed_results_without_replay(fault):
    handler, calls = _counting(fault)
    registry = _registry(GitHubRepoSearch(TOKEN, _client(handler)))
    result = await registry.execute("github.search_repositories", SEARCH_ARGS, owner_id="u")
    assert result.ok is False and result.error
    assert len(calls) == 1  # contained error never replays the request


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(FAULTS))
@pytest.mark.parametrize("kind", ["push", "pr"])
async def test_mutation_network_faults_are_failed_results_without_replay(fault, kind):
    handler, calls = _counting(fault)
    tool = (GitHubPushBranch if kind == "push" else GitHubCreatePullRequest)(TOKEN, _client(handler))
    name, args = ("github.push_branch", PUSH_ARGS) if kind == "push" else ("github.create_pull_request", PR_ARGS)
    result = await _registry(tool).execute(name, args, owner_id="u")
    assert result.ok is False and result.error
    assert "outcome unknown" in result.error  # a write may have landed; model must verify
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_post_success_then_timeout_on_second_call_does_not_replay_the_mutation():
    """Push: GET succeeds, PATCH times out. The PATCH must be attempted exactly once."""
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"object": {"sha": OLD}}, request=request)
        raise httpx.ReadTimeout("slow", request=request)

    tool = GitHubPushBranch(TOKEN, _client(handler))
    result = await _registry(tool).execute("github.push_branch", PUSH_ARGS, owner_id="u")
    assert result.ok is False and "outcome unknown" in result.error
    assert methods == ["GET", "PATCH"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["http500", "http404", "timeout", "connect", "deep_json"])
@pytest.mark.parametrize("which", ["search", "push"])
async def test_error_text_never_carries_url_query_or_credentials(fault, which):
    handler, _ = _counting(fault)
    if which == "search":
        tool, name, args = GitHubRepoSearch(TOKEN, _client(handler)), "github.search_repositories", SEARCH_ARGS
    else:
        tool, name, args = GitHubPushBranch(TOKEN, _client(handler)), "github.push_branch", PUSH_ARGS
    result = await _registry(tool).execute(name, args, owner_id="u")
    assert result.ok is False
    for secret in (TOKEN, QUERY_CANARY, "?q=", "api.github.com/search"):
        assert secret not in result.error
    assert len(result.error) <= 300


@pytest.mark.asyncio
async def test_status_error_names_class_and_status_only():
    handler, _ = _counting("http404")
    registry = _registry(GitHubRepoSearch(TOKEN, _client(handler)))
    result = await registry.execute("github.search_repositories", SEARCH_ARGS, owner_id="u")
    assert result.error.startswith("HTTPStatusError: HTTP 404")


# ---- characterization: previously contained errors and valid results unchanged ----

@pytest.mark.asyncio
async def test_invalid_json_is_still_a_contained_value_error():
    def handler(request):
        return httpx.Response(200, content=b"{not json", request=request)

    registry = _registry(GitHubRepoSearch(TOKEN, _client(handler)))
    result = await registry.execute("github.search_repositories", SEARCH_ARGS, owner_id="u")
    assert result.ok is False and "outcome unknown" not in result.error


@pytest.mark.asyncio
async def test_valid_search_result_unchanged():
    repo = {
        "full_name": "o/r", "html_url": "https://github.com/o/r", "description": "d", "language": "Python",
        "stargazers_count": 5, "forks_count": 1, "open_issues_count": 0, "license": None,
        "updated_at": "2026-01-01T00:00:00Z", "pushed_at": "2026-01-01T00:00:00Z", "archived": False,
    }

    def handler(request):
        return httpx.Response(200, json={"items": [repo]}, request=request)

    registry = _registry(GitHubRepoSearch(TOKEN, _client(handler)))
    result = await registry.execute("github.search_repositories", SEARCH_ARGS, owner_id="u")
    assert result.ok is True and result.content[0]["full_name"] == "o/r"


@pytest.mark.asyncio
async def test_non_network_bug_still_escapes():
    """Containment is network-class only: a tool bug must stay loud."""
    from pydantic import BaseModel

    from meemee.tools.base import Tool

    class Args(BaseModel):
        pass

    class Buggy(Tool):
        name = "buggy.tool"
        description = "raises ZeroDivisionError"
        arguments_model = Args

        async def run(self, arguments):
            return 1 / 0

    with pytest.raises(ZeroDivisionError):
        await _registry(Buggy()).execute("buggy.tool", {}, owner_id="u")

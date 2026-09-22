import httpx
import pytest

from meemee.tools.github import (
    GitHubCreatePullRequest,
    GitHubPullRequestArgs,
    GitHubPushArgs,
    GitHubPushBranch,
)

OLD="a"*40; NEW="b"*40

@pytest.mark.asyncio
async def test_push_uses_optimistic_head_and_exact_ref():
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method=="GET": return httpx.Response(200,json={"object":{"sha":OLD}})
        return httpx.Response(200,json={"object":{"sha":NEW}})
    tool=GitHubPushBranch("token",httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result=await tool.run(GitHubPushArgs(owner="o",repository="r",branch="feature/x",expected_head=OLD,commit_sha=NEW))
    assert result["commit_sha"]==NEW and result["url"].endswith(NEW)
    assert seen[1].method=="PATCH" and seen[1].url.path.endswith("/git/refs/heads/feature/x")

@pytest.mark.asyncio
async def test_push_refuses_changed_head_without_mutation():
    calls=[]
    def handler(request): calls.append(request); return httpx.Response(200,json={"object":{"sha":NEW}})
    tool=GitHubPushBranch("token",httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError,match="remote head changed"):
        await tool.run(GitHubPushArgs(owner="o",repository="r",branch="main",expected_head=OLD,commit_sha=NEW))
    assert len(calls)==1

@pytest.mark.asyncio
async def test_pull_request_returns_canonical_url_and_requires_token():
    async def run():
        def handler(request): return httpx.Response(201,json={"number":7,"html_url":"https://github.com/o/r/pull/7","state":"open","draft":True})
        tool=GitHubCreatePullRequest("token",httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return await tool.run(GitHubPullRequestArgs(owner="o",repository="r",title="Ship feature",head="feature",base="main",draft=True))
    assert (await run())["url"]=="https://github.com/o/r/pull/7"
    with pytest.raises(ValueError,match="MEEMEE_GITHUB_TOKEN"):
        await GitHubCreatePullRequest(None).run(GitHubPullRequestArgs(owner="o",repository="r",title="Ship feature",head="feature",base="main"))

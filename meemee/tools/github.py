from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class GitHubSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=256)
    language: str | None = None
    min_stars: int = Field(default=0, ge=0)
    sort: str = Field(default="best", pattern="^(best|stars|updated)$")
    limit: int = Field(default=10, ge=1, le=30)


class GitHubRepoSearch(Tool):
    name = "github.search_repositories"
    description = "Search GitHub and rank repositories by quality, recency, and popularity."
    arguments_model = GitHubSearchArgs

    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=30)
        self.token = token

    @staticmethod
    def _score(repo: dict[str, Any]) -> float:
        stars = int(repo.get("stargazers_count", 0))
        forks = int(repo.get("forks_count", 0))
        issues = int(repo.get("open_issues_count", 0))
        pushed = datetime.fromisoformat(repo["pushed_at"].replace("Z", "+00:00"))
        age_days = max((datetime.now(timezone.utc) - pushed).days, 0)
        freshness = 100 / (1 + age_days / 30)
        maintenance = max(0, 20 - issues / max(stars + forks, 1) * 100)
        return round((stars + 1) ** 0.35 * 10 + (forks + 1) ** 0.25 * 5 + freshness + maintenance, 2)

    async def run(self, arguments: GitHubSearchArgs) -> list[dict[str, Any]]:
        qualifiers = [arguments.query]
        if arguments.language:
            qualifiers.append(f"language:{arguments.language}")
        if arguments.min_stars:
            qualifiers.append(f"stars:>={arguments.min_stars}")
        api_sort = None if arguments.sort == "best" else arguments.sort
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = await self.client.get(
            "https://api.github.com/search/repositories",
            headers=headers,
            params={"q": " ".join(qualifiers), "sort": api_sort, "per_page": arguments.limit},
        )
        if response.status_code == 403:
            raise ValueError("GitHub rate limit reached; set MEEMEE_GITHUB_TOKEN")
        response.raise_for_status()
        repos = response.json()["items"]
        rows = [
            {
                "full_name": r["full_name"],
                "url": r["html_url"],
                "description": r.get("description"),
                "language": r.get("language"),
                "stars": r["stargazers_count"],
                "forks": r["forks_count"],
                "open_issues": r["open_issues_count"],
                "license": (r.get("license") or {}).get("spdx_id"),
                "updated_at": r["updated_at"],
                "pushed_at": r["pushed_at"],
                "archived": r["archived"],
                "quality_score": self._score(r),
            }
            for r in repos if not r["archived"]
        ]
        if arguments.sort == "best":
            rows.sort(key=lambda row: row["quality_score"], reverse=True)
        return rows[: arguments.limit]

class GitHubPushArgs(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    branch: str = Field(min_length=1, max_length=200)
    expected_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    force: bool = False


class GitHubPullRequestArgs(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=3, max_length=256)
    head: str = Field(min_length=1, max_length=200)
    base: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=50_000)
    draft: bool = False


class GitHubMutation(Tool):
    risk = Risk.WRITE

    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=30)
        self.token = token

    def headers(self) -> dict[str, str]:
        if not self.token: raise ValueError("MEEMEE_GITHUB_TOKEN is required")
        return {"Authorization":f"Bearer {self.token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}

    async def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response=await self.client.request(method,url,headers=self.headers(),**kwargs)
        if response.status_code in {401,403}: raise ValueError("GitHub refused the credential or permission")
        if response.status_code == 422: raise ValueError(f"GitHub rejected mutation: {response.json().get('message','validation failed')}")
        response.raise_for_status()
        return response.json()


class GitHubPushBranch(GitHubMutation):
    name = "github.push_branch"
    description = "Advance one remote branch to an exact commit with optimistic head protection. Requires approval."
    arguments_model = GitHubPushArgs

    async def run(self, arguments: GitHubPushArgs) -> dict[str, Any]:
        root=f"https://api.github.com/repos/{arguments.owner}/{arguments.repository}"
        encoded=httpx.URL(arguments.branch).raw_path.decode().lstrip("/")
        current=await self.request("GET",f"{root}/git/ref/heads/{encoded}")
        actual=current["object"]["sha"]
        if actual != arguments.expected_head:
            raise ValueError(f"remote head changed: expected {arguments.expected_head}, found {actual}")
        updated=await self.request("PATCH",f"{root}/git/refs/heads/{encoded}",json={"sha":arguments.commit_sha,"force":arguments.force})
        return {"branch":arguments.branch,"previous_sha":actual,"commit_sha":updated["object"]["sha"],"url":f"https://github.com/{arguments.owner}/{arguments.repository}/commit/{arguments.commit_sha}"}


class GitHubCreatePullRequest(GitHubMutation):
    name = "github.create_pull_request"
    description = "Create a GitHub pull request after explicit write approval."
    arguments_model = GitHubPullRequestArgs

    async def run(self, arguments: GitHubPullRequestArgs) -> dict[str, Any]:
        data=await self.request("POST",f"https://api.github.com/repos/{arguments.owner}/{arguments.repository}/pulls",json={"title":arguments.title,"head":arguments.head,"base":arguments.base,"body":arguments.body,"draft":arguments.draft})
        return {"number":data["number"],"url":data["html_url"],"state":data["state"],"draft":data["draft"]}

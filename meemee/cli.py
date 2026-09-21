from __future__ import annotations

import asyncio
import getpass
import json

import typer
import uvicorn

from .config import Settings
from .runtime import build_agent
from .tools.github import GitHubRepoSearch, GitHubSearchArgs
from .vault import SecretVault
from .worker import work_forever

app = typer.Typer(no_args_is_help=True, help="Meemee local-first agent runtime")


@app.command()
def run(goal: str, approve_writes: bool = typer.Option(False, "--approve-writes")) -> None:
    """Run an agent goal. Writes require --approve-writes."""
    async def execute():
        agent = build_agent()
        report = await agent.run(goal, approve=lambda _n, _a, _r: approve_writes)
        typer.echo(report.model_dump_json(indent=2))
    asyncio.run(execute())


@app.command()
def scout(query: str, language: str | None = None, min_stars: int = 0, limit: int = 10) -> None:
    """Search and quality-rank live GitHub repositories."""
    async def execute():
        settings = Settings()
        tool = GitHubRepoSearch(settings.github_token)
        rows = await tool.run(GitHubSearchArgs(
            query=query, language=language, min_stars=min_stars, limit=limit
        ))
        typer.echo(json.dumps(rows, indent=2))
    asyncio.run(execute())


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Start the HTTP API."""
    uvicorn.run("meemee.api:app", host=host, port=port)


@app.command()
def worker() -> None:
    """Run a durable queued-job worker."""
    asyncio.run(work_forever())


@app.command("vault-key")
def vault_key() -> None:
    """Generate a new vault key. Store it outside the repository."""
    typer.echo(SecretVault.generate_key())


@app.command("vault-put")
def vault_put(name: str) -> None:
    """Store a secret read without terminal echo."""
    settings = Settings()
    if not settings.vault_key:
        raise typer.BadParameter("MEEMEE_VAULT_KEY is required")
    SecretVault(settings.data_dir / "vault.sqlite3", settings.vault_key).put(name, getpass.getpass("Secret: "))
    typer.echo(f"stored {name}")


@app.command("vault-list")
def vault_list() -> None:
    """List secret names without disclosing values."""
    settings = Settings()
    if not settings.vault_key:
        raise typer.BadParameter("MEEMEE_VAULT_KEY is required")
    for name in SecretVault(settings.data_dir / "vault.sqlite3", settings.vault_key).names():
        typer.echo(name)

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path

import typer
import uvicorn

from .backup import BackupManager
from .config import Settings
from .migrations import CORE_MIGRATIONS, Migrator
from .onboarding import initialize
from .preflight import run_preflight
from .release_audit import audit_tree
from .retention import RetentionManager
from .runtime import build_agent
from .tools.github import GitHubRepoSearch, GitHubSearchArgs
from .vault import SecretVault
from .webhook_verify import verify_signature
from .webhooks import WebhookStore, dispatch_forever
from .worker import work_forever

app = typer.Typer(no_args_is_help=True, help="Meemee local-first agent runtime")
DEFAULT_DATA_DIR = Path.home() / ".meemee"
DEFAULT_ENV_FILE = Path(".env")
DATA_DIR_OPTION = typer.Option(DEFAULT_DATA_DIR, "--data-dir")
ENV_FILE_OPTION = typer.Option(DEFAULT_ENV_FILE, "--env-file")
ROOT_ARGUMENT = typer.Argument(Path("."))


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


@app.command("db-migrate")
def db_migrate() -> None:
    """Apply checksummed forward-only operational schema migrations."""
    settings = Settings()
    applied = Migrator(settings.data_dir / "operations.sqlite3", CORE_MIGRATIONS).migrate()
    typer.echo(json.dumps({"applied": applied}))


@app.command("backup")
def backup(destination: Path) -> None:
    """Create a consistent verified online backup of all SQLite databases."""
    typer.echo(json.dumps(BackupManager(Settings().data_dir).create(destination), indent=2))


@app.command("backup-verify")
def backup_verify(directory: Path) -> None:
    """Verify backup checksums and SQLite integrity."""
    typer.echo(json.dumps(BackupManager.verify(directory), indent=2))


@app.command("retention-run")
def retention_run() -> None:
    """Apply configured retention windows and print deletion counts."""
    settings = Settings()
    report = RetentionManager(settings.data_dir).run(
        jobs_days=settings.retention_jobs_days,
        memory_days=settings.retention_memory_days,
        audit_days=settings.retention_audit_days,
    )
    typer.echo(json.dumps(report.__dict__, indent=2))


@app.command("release-audit")
def release_audit(root: Path = ROOT_ARGUMENT) -> None:
    """Fail when a release tree has stub markers, omissions or version drift."""
    report = audit_tree(root.resolve())
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("init")
def init(
    instance_dir: Path = DATA_DIR_OPTION,
    env_file: Path = ENV_FILE_OPTION,
) -> None:
    """Create a secure first-run instance and configuration without overwrites."""
    try:
        report = initialize(instance_dir, env_file)
    except (FileExistsError, OSError, RuntimeError) as exc:
        typer.echo(json.dumps({"status": "error", "error": str(exc)}))
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(report, indent=2))


@app.command("preflight")
def preflight(require_model: bool = typer.Option(False, "--require-model")) -> None:
    """Validate production configuration, storage and dependencies as JSON."""
    report = asyncio.run(run_preflight(Settings(), require_model=require_model))
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("webhook-worker")
def webhook_worker() -> None:
    """Run the durable signed-webhook dispatcher."""
    settings = Settings()
    asyncio.run(dispatch_forever(WebhookStore(settings.data_dir / "webhooks.sqlite3", encryption_key=settings.vault_key), settings.webhook_poll_seconds))


@app.command("webhook-verify")
def webhook_verify(
    secret_name: str,
    timestamp: str,
    signature: str,
    body_file: Path | None = None,
    tolerance_seconds: int = 300,
) -> None:
    """Verify a received webhook using a vault secret and file/stdin body bytes."""
    settings = Settings()
    if not settings.vault_key:
        raise typer.BadParameter("MEEMEE_VAULT_KEY is required")
    secret = SecretVault(settings.data_dir / "vault.sqlite3", settings.vault_key).get(secret_name)
    body = body_file.read_bytes() if body_file else __import__("sys").stdin.buffer.read()
    valid = verify_signature(secret, timestamp, body, signature, tolerance_seconds)
    typer.echo(json.dumps({"valid": valid}))
    if not valid:
        raise typer.Exit(1)

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path

import typer
import uvicorn

from .account_export import export_account, import_account, inspect_import
from .api_loadcheck import run_api_loadcheck
from .audit import AuditLog
from .audit_anchor import create_anchor, prune_to_anchor, verify_anchor
from .backup import BackupManager
from .companion.cli import companion_app
from .config import Settings
from .key_rotation import rotate_keys
from .loadcheck import run_loadcheck
from .migrations import CORE_MIGRATIONS, Migrator
from .onboarding import initialize
from .package_audit import audit_wheel
from .postgres_copy import export_sqlite, import_postgresql
from .preflight import run_preflight
from .release_audit import audit_tree
from .retention import RetentionManager
from .runtime import build_agent
from .schema_registry import schema_status
from .tools.github import GitHubRepoSearch, GitHubSearchArgs
from .vault import SecretVault
from .webhook_verify import verify_signature
from .webhooks import WebhookStore, dispatch_forever
from .worker import work_forever

app = typer.Typer(no_args_is_help=True, help="Meemee local-first agent runtime")
app.add_typer(companion_app, name="companion")
models_app = typer.Typer(no_args_is_help=True, help="Model profiles and routing (local, Inkling, Fugu, custom)")
app.add_typer(models_app, name="models")


@models_app.command("list")
def models_list() -> None:
    """Show every model profile, whether it is usable, and the active routes."""
    from .model_profiles import ModelCatalog

    catalog = ModelCatalog.from_settings(Settings())
    typer.echo(json.dumps({
        "allow_paid_models": catalog.allow_paid,
        "routes": catalog.routes,
        "profiles": [p.public_dict(catalog.allow_paid) for p in catalog.profiles.values()],
    }, indent=2))


@models_app.command("check")
def models_check(name: list[str] = typer.Argument(None)) -> None:  # noqa: B008
    """Probe each profile's /models endpoint without spending tokens."""
    from .model_profiles import ModelCatalog, probe_profile

    catalog = ModelCatalog.from_settings(Settings())
    targets = name or list(catalog.profiles)
    unknown = [n for n in targets if n not in catalog.profiles]
    if unknown:
        raise typer.BadParameter(f"unknown profiles: {', '.join(unknown)}")

    async def _probe() -> list[dict]:
        out = []
        for n in targets:
            profile = catalog.profiles[n]
            reason = profile.unavailable_reason(catalog.allow_paid)
            out.append({"name": n, "reachable": False, "skipped": reason} if reason else await probe_profile(profile))
        return out

    results = asyncio.run(_probe())
    typer.echo(json.dumps(results, indent=2))
    if not any(r.get("reachable") for r in results):
        raise typer.Exit(1)


@models_app.command("inkling-local")
def models_inkling_local(
    plan: str = typer.Option("auto", help="auto or one of: vllm-nvfp4, vllm-bf16, llamacpp-q4, llamacpp-q3, llamacpp-q2"),
    model_dir: Path = typer.Option(Path("models/inkling-small"), help="Where GGUF weights live"),  # noqa: B008
    port: int = 8000,
    run: bool = typer.Option(False, "--run", help="Download weights if needed and start the server"),
    force: bool = typer.Option(False, "--force", help="Start even if this machine is below the floor"),
) -> None:
    """Check this machine against Inkling-Small's hardware floor and print or run the exact server command."""
    import os
    import subprocess

    from .inkling_local import (
        PLANS,
        VLLM_ENV,
        assess,
        detect_hardware,
        download_command,
        gaps,
        launch_command,
    )

    hw = detect_hardware(model_dir)
    report = assess(hw)
    chosen = report["recommended"] if plan == "auto" else plan
    if chosen is not None and chosen not in PLANS:
        raise typer.BadParameter(f"unknown plan {plan!r}")
    if chosen is None and plan == "auto":
        chosen = "llamacpp-q2" if force else None
    if chosen:
        report["plan"] = chosen
        report["plan_gaps"] = gaps(PLANS[chosen], hw)
        report["download"] = download_command(chosen, str(model_dir))
        report["launch"] = launch_command(chosen, hw, port, str(model_dir))
        report["meemee_env"] = {
            "MEEMEE_INKLING_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "MEEMEE_MODEL_ROUTES": "agent=inkling-vllm,inkling,local;chat=inkling-vllm,inkling,local",
        }
    typer.echo(json.dumps(report, indent=2))
    if not run:
        return
    if not chosen or (report["plan_gaps"] and not force):
        typer.echo("Refusing to start: this machine is below the floor for that plan (use --force to try anyway).", err=True)
        raise typer.Exit(2)
    env = {**os.environ, **(VLLM_ENV if PLANS[chosen].engine == "vllm" else {})}
    if report["download"] and not any(model_dir.glob("**/*.gguf")):
        subprocess.run(report["download"], check=True, env=env)
    raise typer.Exit(subprocess.run(report["launch"], env=env, check=False).returncode)
# end models commands


@app.command("reflection-worker")
def reflection_worker() -> None:
    """Run scheduled personal-model reflection on the reflection model route."""
    from .reflection_schedule import reflection_forever

    asyncio.run(reflection_forever())
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


@app.command("rotate-encryption-key")
def rotate_encryption_key() -> None:
    """Re-encrypt vault and webhook secrets using a new key read without echo."""
    settings = Settings()
    if not settings.vault_key:
        raise typer.BadParameter("MEEMEE_VAULT_KEY with the current key is required")
    new_key = getpass.getpass("New MEEMEE_VAULT_KEY: ")
    confirmation = getpass.getpass("Confirm new key: ")
    if not new_key or new_key != confirmation:
        raise typer.BadParameter("new key confirmation does not match")
    report = rotate_keys(settings.data_dir, settings.vault_key, new_key)
    typer.echo(json.dumps({**report, "status":"rotated", "next":"replace MEEMEE_VAULT_KEY and restart all processes"}))


@app.command("vault-list")
def vault_list() -> None:
    """List secret names without disclosing values."""
    settings = Settings()
    if not settings.vault_key:
        raise typer.BadParameter("MEEMEE_VAULT_KEY is required")
    for name in SecretVault(settings.data_dir / "vault.sqlite3", settings.vault_key).names():
        typer.echo(name)


@app.command("schema-status")
def schema_status_command() -> None:
    """Report component schema versions/checksums across all SQLite databases."""
    import sqlite3

    settings = Settings()
    databases = []
    for path in sorted(settings.data_dir.glob("*.sqlite3")):
        connection = sqlite3.connect(path)
        try:
            components = schema_status(connection)
        finally:
            connection.close()
        if components:
            databases.append({"database": path.name, "components": components})
    typer.echo(json.dumps({"databases": databases}, indent=2))


@app.command("postgres-copy-export")
def postgres_copy_export(destination: Path) -> None:
    """Export checksummed SQLite core data for PostgreSQL cutover."""
    typer.echo(json.dumps(export_sqlite(Settings().data_dir, destination), indent=2))


@app.command("postgres-copy-import")
def postgres_copy_import(source: Path) -> None:
    """Import a verified export into empty PostgreSQL tables transactionally."""
    settings=Settings()
    if not settings.postgres_dsn: raise typer.BadParameter("MEEMEE_POSTGRES_DSN is required")
    from meemee_persist_pg import Database, MigrationStore
    database=Database(settings.postgres_dsn)
    try:
        MigrationStore(database).apply()
        typer.echo(json.dumps(import_postgresql(source,database),indent=2))
    finally: database.close()


@app.command("db-migrate")
def db_migrate() -> None:
    """Apply checksummed forward-only operational schema migrations."""
    settings = Settings()
    applied = Migrator(settings.data_dir / "operations.sqlite3", CORE_MIGRATIONS).migrate()
    typer.echo(json.dumps({"applied": applied}))


@app.command("account-export")
def account_export(principal: str, destination: Path) -> None:
    """Export account-owned jobs, runs and entitlement metadata with checksum."""
    typer.echo(json.dumps(export_account(Settings().data_dir, principal, destination), indent=2))


@app.command("account-import")
def account_import(source: Path, target_principal: str | None = None) -> None:
    """Import a verified account export after collision preflight."""
    typer.echo(json.dumps(import_account(Settings().data_dir, source, target_principal), indent=2))


@app.command("account-import-inspect")
def account_import_inspect(source: Path, target_principal: str | None = None) -> None:
    """Verify an account export and print a no-write import plan."""
    report = inspect_import(source, target_principal)
    report.pop("payload")
    typer.echo(json.dumps({**report, "dry_run":True}, indent=2))


@app.command("audit-anchor")
def audit_anchor(destination: Path) -> None:
    """Write a signed tamper-evident audit checkpoint to external storage."""
    settings = Settings()
    if not settings.audit_anchor_key:
        raise typer.BadParameter("MEEMEE_AUDIT_ANCHOR_KEY is required")
    report = create_anchor(AuditLog(settings.data_dir / "audit.sqlite3"), destination, settings.audit_anchor_key)
    typer.echo(json.dumps(report, indent=2))


@app.command("audit-prune")
def audit_prune(anchor: Path) -> None:
    """Prune only a prefix protected by a valid external signed checkpoint."""
    settings = Settings()
    if not settings.audit_anchor_key:
        raise typer.BadParameter("MEEMEE_AUDIT_ANCHOR_KEY is required")
    report = prune_to_anchor(AuditLog(settings.data_dir / "audit.sqlite3"), anchor, settings.audit_anchor_key)
    typer.echo(json.dumps(report, indent=2))


@app.command("audit-anchor-verify")
def audit_anchor_verify(source: Path) -> None:
    """Verify an external audit checkpoint against the current local chain."""
    settings = Settings()
    if not settings.audit_anchor_key:
        raise typer.BadParameter("MEEMEE_AUDIT_ANCHOR_KEY is required")
    report = verify_anchor(AuditLog(settings.data_dir / "audit.sqlite3"), source, settings.audit_anchor_key)
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("backup")
def backup(destination: Path) -> None:
    """Create a consistent verified online backup of all SQLite databases."""
    typer.echo(json.dumps(BackupManager(Settings().data_dir).create(destination), indent=2))


@app.command("backup-restore")
def backup_restore(directory: Path, destination: Path) -> None:
    """Verify and restore a backup into a new empty data directory."""
    typer.echo(json.dumps(BackupManager.restore(directory, destination), indent=2))


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
        runs_days=settings.retention_runs_days,
    )
    typer.echo(json.dumps(report.__dict__, indent=2))


@app.command("package-audit")
def package_audit(wheel: Path, expected_version: str | None = None) -> None:
    """Verify a built wheel contains the CLI, typed package and web console."""
    from . import __version__

    report = audit_wheel(wheel, expected_version or __version__)
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise typer.Exit(1)


@app.command("release-audit")
def release_audit(
    root: Path = ROOT_ARGUMENT,
    expected_version: str | None = typer.Option(None, "--expected-version"),
) -> None:
    """Fail on release defects; expected-version prevents stale aligned metadata."""
    report = audit_tree(root.resolve(), expected_version)
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


@app.command("api-loadcheck")
def api_loadcheck(requests: int = 200, concurrency: int = 20) -> None:
    """Run parallel requests through the complete in-process API stack."""
    settings = Settings()
    if not settings.api_token:
        raise typer.BadParameter("MEEMEE_API_TOKEN is required")
    from .api import app as api_app
    report = asyncio.run(run_api_loadcheck(api_app, settings.api_token, requests, concurrency))
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass": raise typer.Exit(1)


@app.command("loadcheck")
def loadcheck(operations: int = 1_000, workers: int = 16) -> None:
    """Run the release-blocking shared-store contention harness."""
    report = run_loadcheck(operations, workers)
    typer.echo(json.dumps(report, indent=2))
    if report["status"] != "pass": raise typer.Exit(1)


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
    store = WebhookStore(
        settings.data_dir / "webhooks.sqlite3",
        settings.webhook_max_payload_bytes,
        settings.vault_key,
    )
    asyncio.run(dispatch_forever(store, settings.webhook_poll_seconds))


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

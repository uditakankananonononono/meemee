from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import httpx

from .config import Settings
from .secret_cipher import SecretCipher


def _result(name: str, ok: bool, severity: str = "error", **detail: object) -> dict:
    return {"name": name, "ok": ok, "severity": severity, **detail}


def _check_data_dir(path: Path, minimum_free_bytes: int) -> list[dict]:
    checks: list[dict] = []
    target = path if path.exists() else path.parent
    writable = target.is_dir() and os.access(target, os.W_OK | os.X_OK)
    checks.append(_result("data_directory_writable", writable, path=str(path)))
    if target.exists():
        usage = os.statvfs(target)
        free = usage.f_bavail * usage.f_frsize
        checks.append(_result(
            "data_directory_free_space",
            free >= minimum_free_bytes,
            free_bytes=free,
            minimum_bytes=minimum_free_bytes,
        ))
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        checks.append(_result(
            "data_directory_permissions",
            not bool(mode & stat.S_IWOTH),
            mode=oct(mode),
            remediation="remove world-write permission" if mode & stat.S_IWOTH else None,
        ))
    return checks


def _check_databases(path: Path) -> list[dict]:
    checks: list[dict] = []
    for database in sorted(path.glob("*.sqlite3")) if path.exists() else []:
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2)
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            connection.close()
            checks.append(_result(
                f"database_integrity:{database.name}", integrity == "ok", result=integrity
            ))
        except sqlite3.Error as exc:
            checks.append(_result(
                f"database_integrity:{database.name}", False, error=type(exc).__name__
            ))
    return checks


async def run_preflight(
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    require_model: bool | None = None,
) -> dict:
    """Validate a production configuration without exposing configured secrets."""
    checks: list[dict] = []
    checks.append(_result(
        "bootstrap_api_token",
        bool(settings.api_token and len(settings.api_token) >= 32),
        remediation="set MEEMEE_API_TOKEN to at least 32 random characters",
    ))
    vault_ok = False
    if settings.vault_key:
        try:
            SecretCipher(settings.vault_key)
            vault_ok = True
        except ValueError:
            pass
    checks.append(_result(
        "vault_key", vault_ok, remediation="set MEEMEE_VAULT_KEY from `meemee vault-key`"
    ))
    checks.extend(_check_data_dir(settings.data_dir, settings.readiness_min_free_bytes))
    checks.extend(_check_databases(settings.data_dir))
    if settings.persistence_backend.lower() == "postgresql":
        postgres_ok = False
        error = None
        if not settings.postgres_dsn:
            error = "MEEMEE_POSTGRES_DSN is required"
        else:
            try:
                from meemee_persist_pg import Database, MigrationStore
                database = Database(settings.postgres_dsn, min_size=1, max_size=2, timeout=5)
                try:
                    pending = MigrationStore(database).pending()
                    postgres_ok = not pending
                    error = f"pending migrations: {pending}" if pending else None
                finally: database.close()
            except (RuntimeError, OSError, ValueError) as exc:
                error = f"{type(exc).__name__}: {exc}"
        checks.append(_result("postgresql_connection_and_schema", postgres_ok, error=error))
        checks.append(_result("deployment_boundary", True, "warning", supported_shape="PostgreSQL memory and owner-scoped jobs; validate remaining SQLite commercial stores per host"))
    elif settings.persistence_backend.lower() == "sqlite":
        checks.append(_result("deployment_boundary", True, "warning", supported_shape="SQLite/WAL on one host; do not share the data directory across hosts"))
    else:
        checks.append(_result("persistence_backend", False, configured=settings.persistence_backend, remediation="set sqlite or postgresql"))
    model_required = settings.readiness_require_model if require_model is None else require_model
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2))
    try:
        response = await client.get(f"{settings.model_base_url.rstrip('/')}/models")
        model_ok = response.status_code < 400
        checks.append(_result(
            "model_endpoint", model_ok, "error" if model_required else "warning",
            status=response.status_code,
        ))
    except httpx.HTTPError as exc:
        checks.append(_result(
            "model_endpoint", False, "error" if model_required else "warning",
            error=type(exc).__name__,
        ))
    finally:
        if owns_client:
            await client.aclose()
    failures = [check for check in checks if not check["ok"] and check["severity"] == "error"]
    warnings = [check for check in checks if not check["ok"] and check["severity"] == "warning"]
    return {
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "summary": {"passed": sum(check["ok"] for check in checks), "failed": len(failures), "warnings": len(warnings)},
    }

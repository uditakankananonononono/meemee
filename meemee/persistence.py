from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore as SQLiteApprovalStore
from .audit import AuditLog as SQLiteAuditLog
from .auth import TokenStore as SQLiteTokenStore
from .email_verification import EmailVerificationStore as SQLiteEmailVerificationStore
from .entitlements import EntitlementStore as SQLiteEntitlementStore
from .idempotency import IdempotencyStore as SQLiteIdempotencyStore
from .jobs import JobStore as SQLiteJobStore
from .memory import MemoryStore as SQLiteMemoryStore
from .quotas import QuotaStore as SQLiteQuotaStore
from .runs import RunStore as SQLiteRunStore
from .webhooks import WebhookStore as SQLiteWebhookStore


@dataclass
class Persistence:
    backend: str
    memory: Any
    jobs: Any
    close: Any
    database: Any = None
    #: Tool-approval grants. SQLite: approvals.sqlite3 in the data directory (single host).
    #: PostgreSQL: meemee_tool_approvals, shared by the API and every worker on the database.
    approvals: Any = None
    #: API tokens + customer accounts, and the tamper-evident audit chain. SQLite: auth.sqlite3 and
    #: audit.sqlite3 in the data directory. PostgreSQL: shared tables, one global audit chain.
    tokens: Any = None
    audit: Any = None
    #: Email-verification and password-reset challenges. SQLite: email-verifications.sqlite3.
    #: PostgreSQL: meemee_email_verifications / meemee_password_resets, so links work on any host.
    email_verifications: Any = None
    #: Daily job quotas and plan assignments. SQLite: quotas.sqlite3 / entitlements.sqlite3.
    #: PostgreSQL: one counter per (principal, UTC day) and one plan row, enforced on every host.
    quotas: Any = None
    entitlements: Any = None
    #: Completed run reports and idempotency records. SQLite: runs.sqlite3 / idempotency.sqlite3.
    #: PostgreSQL: shared, so runs are visible and idempotent retries dedupe on every host.
    runs: Any = None
    idempotency: Any = None
    #: Webhook subscriptions and delivery outbox. SQLite: webhooks.sqlite3. PostgreSQL: shared by every
    #: API host, worker and dispatcher. None when no vault key is configured (secrets are encrypted).
    webhooks: Any = None

    def check_memory(self) -> bool:
        if self.backend == "sqlite":
            return self.memory.connection.execute("SELECT 1").fetchone() is not None
        with self.memory.db.transaction() as connection:
            return connection.execute("SELECT 1").fetchone() is not None

    def check_jobs(self) -> bool:
        if self.backend == "sqlite":
            return self.jobs.db.execute("SELECT 1").fetchone() is not None
        with self.jobs.db.transaction() as connection:
            return connection.execute("SELECT 1").fetchone() is not None


def build_persistence(backend: str, data_dir: Path, postgres_dsn: str | None = None, *,
                      default_daily_jobs: int = 100, default_plan: str = "starter",
                      vault_key: str | None = None, webhook_max_payload_bytes: int = 256_000) -> Persistence:
    """Select and initialize the supported persistence composition root."""
    normalized = backend.strip().lower()
    if normalized == "sqlite":
        return Persistence("sqlite", SQLiteMemoryStore(data_dir / "meemee.sqlite3"), SQLiteJobStore(data_dir / "jobs.sqlite3"), lambda: None,
                           approvals=SQLiteApprovalStore(data_dir / "approvals.sqlite3"),
                           tokens=SQLiteTokenStore(data_dir / "auth.sqlite3"), audit=SQLiteAuditLog(data_dir / "audit.sqlite3"),
                           email_verifications=SQLiteEmailVerificationStore(data_dir / "email-verifications.sqlite3"),
                           quotas=SQLiteQuotaStore(data_dir / "quotas.sqlite3", default_daily_jobs),
                           entitlements=SQLiteEntitlementStore(data_dir / "entitlements.sqlite3", default_plan),
                           runs=SQLiteRunStore(data_dir / "runs.sqlite3"), idempotency=SQLiteIdempotencyStore(data_dir / "idempotency.sqlite3"),
                           webhooks=SQLiteWebhookStore(data_dir / "webhooks.sqlite3", webhook_max_payload_bytes, vault_key) if vault_key else None)
    if normalized != "postgresql":
        raise ValueError("MEEMEE_PERSISTENCE_BACKEND must be sqlite or postgresql")
    if not postgres_dsn:
        raise ValueError("MEEMEE_POSTGRES_DSN is required for the postgresql backend")
    from meemee_persist_pg import (
        ApprovalStore,
        AuditLog,
        Database,
        EmailVerificationStore,
        EntitlementStore,
        IdempotencyStore,
        JobStore,
        MemoryStore,
        MigrationStore,
        QuotaStore,
        RunStore,
        TokenStore,
        WebhookStore,
    )
    database = Database(postgres_dsn)
    MigrationStore(database).apply()
    return Persistence("postgresql", MemoryStore(database), JobStore(database), database.close, database,
                       approvals=ApprovalStore(database), tokens=TokenStore(database), audit=AuditLog(database),
                       email_verifications=EmailVerificationStore(database),
                       quotas=QuotaStore(database, default_daily_jobs), entitlements=EntitlementStore(database, default_plan),
                       runs=RunStore(database), idempotency=IdempotencyStore(database),
                       webhooks=WebhookStore(database, webhook_max_payload_bytes, vault_key) if vault_key else None)


def persistence_from_settings(settings: Any) -> Persistence:
    """``build_persistence`` with the product defaults (quota, plan) taken from settings."""
    return build_persistence(settings.persistence_backend, settings.data_dir, settings.postgres_dsn,
                             default_daily_jobs=settings.default_daily_jobs, default_plan=settings.default_plan,
                             vault_key=settings.vault_key, webhook_max_payload_bytes=settings.webhook_max_payload_bytes)

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore as SQLiteApprovalStore
from .audit import AuditLog as SQLiteAuditLog
from .auth import TokenStore as SQLiteTokenStore
from .email_verification import EmailVerificationStore as SQLiteEmailVerificationStore
from .jobs import JobStore as SQLiteJobStore
from .memory import MemoryStore as SQLiteMemoryStore


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


def build_persistence(backend: str, data_dir: Path, postgres_dsn: str | None = None) -> Persistence:
    """Select and initialize the supported persistence composition root."""
    normalized = backend.strip().lower()
    if normalized == "sqlite":
        return Persistence("sqlite", SQLiteMemoryStore(data_dir / "meemee.sqlite3"), SQLiteJobStore(data_dir / "jobs.sqlite3"), lambda: None,
                           approvals=SQLiteApprovalStore(data_dir / "approvals.sqlite3"),
                           tokens=SQLiteTokenStore(data_dir / "auth.sqlite3"), audit=SQLiteAuditLog(data_dir / "audit.sqlite3"),
                           email_verifications=SQLiteEmailVerificationStore(data_dir / "email-verifications.sqlite3"))
    if normalized != "postgresql":
        raise ValueError("MEEMEE_PERSISTENCE_BACKEND must be sqlite or postgresql")
    if not postgres_dsn:
        raise ValueError("MEEMEE_POSTGRES_DSN is required for the postgresql backend")
    from meemee_persist_pg import (
        ApprovalStore,
        AuditLog,
        Database,
        EmailVerificationStore,
        JobStore,
        MemoryStore,
        MigrationStore,
        TokenStore,
    )
    database = Database(postgres_dsn)
    MigrationStore(database).apply()
    return Persistence("postgresql", MemoryStore(database), JobStore(database), database.close, database,
                       approvals=ApprovalStore(database), tokens=TokenStore(database), audit=AuditLog(database),
                       email_verifications=EmailVerificationStore(database))

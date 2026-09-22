from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jobs import JobStore as SQLiteJobStore
from .memory import MemoryStore as SQLiteMemoryStore


@dataclass
class Persistence:
    backend: str
    memory: Any
    jobs: Any
    close: Any

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
        return Persistence("sqlite", SQLiteMemoryStore(data_dir / "meemee.sqlite3"), SQLiteJobStore(data_dir / "jobs.sqlite3"), lambda: None)
    if normalized != "postgresql":
        raise ValueError("MEEMEE_PERSISTENCE_BACKEND must be sqlite or postgresql")
    if not postgres_dsn:
        raise ValueError("MEEMEE_POSTGRES_DSN is required for the postgresql backend")
    from meemee_persist_pg import Database, JobStore, MemoryStore, MigrationStore
    database = Database(postgres_dsn)
    MigrationStore(database).apply()
    return Persistence("postgresql", MemoryStore(database), JobStore(database), database.close)

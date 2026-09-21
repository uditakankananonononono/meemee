from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


class Migrator:
    """Transactional, checksummed, forward-only SQLite schema migrations."""

    def __init__(self, database: Path, migrations: Iterable[Migration]):
        self.database = database
        self.migrations = sorted(migrations, key=lambda item: item.version)
        versions = [item.version for item in self.migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("migration versions must be contiguous from 1")

    def migrate(self, target: int | None = None) -> list[int]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        applied: list[int] = []
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )""")
            current = {row[0]: row[1] for row in connection.execute("SELECT version,checksum FROM schema_migrations")}
            for migration in self.migrations:
                if migration.version in current and current[migration.version] != migration.checksum:
                    raise RuntimeError(f"migration {migration.version} checksum changed after application")
                if migration.version in current or (target is not None and migration.version > target):
                    continue
                with connection:
                    connection.executescript(migration.sql)
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES(?,?,?,?)",
                        (migration.version, migration.name, migration.checksum, datetime.now(timezone.utc).isoformat()),
                    )
                applied.append(migration.version)
        finally:
            connection.close()
        return applied


CORE_MIGRATIONS = [
    Migration(1, "operations", """
        CREATE TABLE IF NOT EXISTS operational_metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """),
]

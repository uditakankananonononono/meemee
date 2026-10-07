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

    @staticmethod
    def _execute_script(connection: sqlite3.Connection, sql: str) -> None:
        # executescript implicitly commits the caller's transaction. Execute
        # complete SQLite statements separately, including trigger bodies.
        forbidden = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                     sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}

        def authorize(action, arg1, arg2, database, source):
            if action in forbidden or arg1 == "schema_migrations":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        try:
            statement = ""
            for char in sql:
                statement += char
                if char == ";" and sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
            if statement.strip():
                connection.execute(statement)
        finally:
            connection.set_authorizer(lambda *args: sqlite3.SQLITE_OK)

    def migrate(self, target: int | None = None) -> list[int]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        applied: list[int] = []
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )""")
            connection.execute("BEGIN IMMEDIATE")
            current = {row[0]: row[1] for row in connection.execute("SELECT version,checksum FROM schema_migrations")}
            for migration in self.migrations:
                if migration.version in current and current[migration.version] != migration.checksum:
                    raise RuntimeError(f"migration {migration.version} checksum changed after application")
                if migration.version in current or (target is not None and migration.version > target):
                    continue
                self._execute_script(connection, migration.sql)
                connection.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?,?)",
                    (migration.version, migration.name, migration.checksum, datetime.now(timezone.utc).isoformat()),
                )
                applied.append(migration.version)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
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

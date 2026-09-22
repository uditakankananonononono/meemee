from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files

from ._db import Database

_LOCK = 6758712042962291

@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def bundled_migrations() -> list[Migration]:
    root = files("meemee_persist_pg").joinpath("sql")
    result = []
    for item in sorted(root.iterdir(), key=lambda p: p.name):
        if item.name.endswith(".sql"):
            number, name = item.name[:-4].split("_", 1)
            result.append(Migration(int(number), name, item.read_text(encoding="utf-8")))
    return result


class MigrationStore:
    """Forward-only checksummed migrator serialized by an advisory lock."""
    def __init__(self, db: Database, migrations: list[Migration] | None = None):
        self.db, self.migrations = db, migrations or bundled_migrations()
        versions = [m.version for m in self.migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("migration versions must be contiguous from 1")

    def _ensure(self, conn) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS meemee_schema_migrations(
          version integer PRIMARY KEY, name text NOT NULL, checksum char(64) NOT NULL,
          applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")

    def pending(self) -> list[int]:
        with self.db.transaction() as conn:
            self._ensure(conn)
            rows = conn.execute("SELECT version,checksum FROM meemee_schema_migrations").fetchall()
            current = {r["version"]: r["checksum"] for r in rows}
            self._validate(current)
            return [m.version for m in self.migrations if m.version not in current]

    def _validate(self, current: dict[int, str]) -> None:
        known = {m.version: m for m in self.migrations}
        unknown = sorted(set(current) - set(known))
        if unknown:
            raise RuntimeError(f"database has unknown migration versions: {unknown}")
        for version, checksum in current.items():
            if checksum != known[version].checksum:
                raise RuntimeError(f"migration {version} checksum changed after application")

    def apply(self, target: int | None = None) -> list[int]:
        applied = []
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK,))
            self._ensure(conn)
            rows = conn.execute("SELECT version,checksum FROM meemee_schema_migrations FOR UPDATE").fetchall()
            current = {r["version"]: r["checksum"] for r in rows}
            self._validate(current)
            for migration in self.migrations:
                if migration.version in current or (target is not None and migration.version > target):
                    continue
                conn.execute(migration.sql, prepare=False)
                conn.execute("INSERT INTO meemee_schema_migrations(version,name,checksum) VALUES(%s,%s,%s)",
                             (migration.version, migration.name, migration.checksum))
                applied.append(migration.version)
        return applied

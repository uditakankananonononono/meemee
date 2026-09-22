from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone


def schema_checksum(statements: list[str]) -> str:
    return hashlib.sha256("\n".join(statements).encode()).hexdigest()


def register_schema(
    connection: sqlite3.Connection,
    component: str,
    version: int,
    statements: list[str],
) -> None:
    """Record and drift-check a component's current idempotent schema contract."""
    checksum = schema_checksum(statements)
    connection.execute("""CREATE TABLE IF NOT EXISTS component_schema_versions(
        component TEXT PRIMARY KEY, version INTEGER NOT NULL, checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )""")
    row = connection.execute(
        "SELECT version,checksum FROM component_schema_versions WHERE component=?", (component,)
    ).fetchone()
    if row is not None:
        if int(row[0]) > version:
            raise RuntimeError(f"{component} database schema is newer than this binary")
        if int(row[0]) == version and row[1] != checksum:
            raise RuntimeError(f"{component} schema checksum changed at version {version}")
        if int(row[0]) == version:
            return
    with connection:
        connection.execute(
            "INSERT INTO component_schema_versions VALUES(?,?,?,?) ON CONFLICT(component) DO UPDATE SET version=excluded.version,checksum=excluded.checksum,applied_at=excluded.applied_at",
            (component, version, checksum, datetime.now(timezone.utc).isoformat()),
        )


def schema_status(connection: sqlite3.Connection) -> list[dict]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='component_schema_versions'"
    ).fetchone()
    if not exists:
        return []
    rows = connection.execute(
        "SELECT component,version,checksum,applied_at FROM component_schema_versions ORDER BY component"
    ).fetchall()
    return [
        {"component": row[0], "version": row[1], "checksum": row[2], "applied_at": row[3]}
        for row in rows
    ]

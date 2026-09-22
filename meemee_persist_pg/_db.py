from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from psycopg import Connection
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError as exc:  # explicit install failure, not a silent fallback
    raise RuntimeError("meemee_persist_pg requires psycopg[binary,pool]>=3.2") from exc


class Database:
    """Bounded PostgreSQL pool; every store operation owns one transaction."""
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10, timeout: float = 10.0):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size,
                                   timeout=timeout, kwargs={"row_factory": dict_row}, open=True)
        self.pool.wait(timeout=timeout)

    @contextmanager
    def transaction(self, *, isolation: str | None = None) -> Iterator[Connection[Any]]:
        with self.pool.connection() as conn, conn.transaction():
            if isolation:
                conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
            yield conn

    def close(self) -> None:
        self.pool.close()

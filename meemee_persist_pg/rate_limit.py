from __future__ import annotations

import time

from ._db import Database


class PostgreSQLRateLimiter:
    """Atomic fixed-window limiter shared by every process using one PostgreSQL database."""
    def __init__(self, database: Database, limit: int = 60, window_seconds: int = 60):
        if limit < 1 or window_seconds < 1: raise ValueError("rate limit and window must be positive")
        self.db,self.limit,self.window=database,limit,window_seconds
        with self.db.transaction() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS meemee_rate_limits(
              identity text NOT NULL, window_start bigint NOT NULL, count integer NOT NULL,
              PRIMARY KEY(identity,window_start))""")

    def hit(self, identity: str, now: float | None = None) -> tuple[bool,int,int]:
        timestamp=int(time.time() if now is None else now); start=timestamp-timestamp%self.window; reset=start+self.window
        with self.db.transaction() as connection:
            row=connection.execute("""INSERT INTO meemee_rate_limits(identity,window_start,count) VALUES(%s,%s,1)
              ON CONFLICT(identity,window_start) DO UPDATE SET count=meemee_rate_limits.count+1 RETURNING count""",(identity,start)).fetchone()
        count=int(row["count"]); return count<=self.limit,max(self.limit-count,0),reset

    def cleanup(self, now: float | None = None) -> int:
        cutoff=int(time.time() if now is None else now)-self.window*2
        with self.db.transaction() as connection:return int(connection.execute("DELETE FROM meemee_rate_limits WHERE window_start<%s",(cutoff,)).rowcount)

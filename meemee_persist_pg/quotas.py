"""PostgreSQL daily job quotas: the same contract as ``meemee.quotas.QuotaStore``.

With per-host SQLite quotas, N API hosts each allowed the full daily limit, so a principal could
submit N times its quota. Here every host increments one row per (principal, UTC day) with an
atomic upsert, and the increment is rolled back when it would exceed the limit, so the limit holds
across hosts under concurrent submissions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from meemee.quotas import QuotaExceeded

from ._db import Database


def _day(now: datetime | None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


class _Rollback(Exception):
    pass


class QuotaStore:
    """Atomic per-principal daily quotas shared by every host on the database."""

    def __init__(self, db: Database, default_daily_jobs: int = 100):
        if default_daily_jobs < 1:
            raise ValueError("default quota must be positive")
        self.db, self.default = db, default_daily_jobs

    def _limit(self, c, principal: str) -> int:
        row = c.execute("SELECT daily_jobs FROM meemee_quota_limits WHERE principal=%s", (principal,)).fetchone()
        return int(row["daily_jobs"]) if row else self.default

    def limit(self, principal: str) -> int:
        with self.db.transaction() as c:
            return self._limit(c, principal)

    def set_limit(self, principal: str, daily_jobs: int) -> None:
        if daily_jobs < 1:
            raise ValueError("daily quota must be positive")
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_quota_limits(principal, daily_jobs) VALUES (%s, %s)
                         ON CONFLICT (principal) DO UPDATE SET daily_jobs = excluded.daily_jobs""", (principal, daily_jobs))

    def consume_job(self, principal: str, now: datetime | None = None) -> dict[str, int | str]:
        day = _day(now)
        try:
            with self.db.transaction() as c:
                # The upsert takes the row lock; the limit is read after it, inside the same transaction.
                used = int(c.execute("""INSERT INTO meemee_quota_usage(principal, day, jobs) VALUES (%s, %s, 1)
                                        ON CONFLICT (principal, day) DO UPDATE SET jobs = meemee_quota_usage.jobs + 1
                                        RETURNING jobs""", (principal, day)).fetchone()["jobs"])
                maximum = self._limit(c, principal)
                if used > maximum:
                    raise _Rollback(maximum)
        except _Rollback as exc:
            raise QuotaExceeded(f"daily job quota exceeded ({exc.args[0]})") from None
        return {"day": day.isoformat(), "used": used, "limit": maximum, "remaining": maximum - used}

    def status(self, principal: str, now: datetime | None = None) -> dict[str, int | str]:
        day = _day(now)
        with self.db.transaction() as c:
            maximum = self._limit(c, principal)
            row = c.execute("SELECT jobs FROM meemee_quota_usage WHERE principal=%s AND day=%s", (principal, day)).fetchone()
        used = int(row["jobs"]) if row else 0
        return {"day": day.isoformat(), "used": used, "limit": maximum, "remaining": max(maximum - used, 0)}

    def delete_principal(self, principal: str) -> dict[str, int]:
        with self.db.transaction() as c:
            usage = c.execute("DELETE FROM meemee_quota_usage WHERE principal=%s", (principal,)).rowcount
            limits = c.execute("DELETE FROM meemee_quota_limits WHERE principal=%s", (principal,)).rowcount
        return {"quota_usage": usage, "quota_limits": limits}

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_quota_usage LIMIT 0")
        return True

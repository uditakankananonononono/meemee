from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class RetentionReport:
    job_events: int = 0
    terminal_jobs: int = 0
    memories: int = 0
    runs: int = 0
    audit_entries: int = 0
    idempotency: int = 0
    rate_limits: int = 0
    webhook_delivered: int = 0
    webhook_failed: int = 0


class RetentionManager:
    """Applies explicit data-retention windows in bounded transactions."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    @staticmethod
    def delete(path: Path, statement: str, parameters: tuple) -> int:
        if not path.exists():
            return 0
        db = sqlite3.connect(path, timeout=5)
        try:
            with db:
                return db.execute(statement, parameters).rowcount
        finally:
            db.close()

    def _delete_jobs(self, path: Path, cutoff: str) -> tuple[int, int]:
        """Remove terminal jobs and their events in one SQLite transaction."""
        if not path.exists():
            return 0, 0
        db = sqlite3.connect(path, timeout=5)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                events = db.execute(
                    "DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs "
                    "WHERE status IN ('done','failed','cancelled') AND updated_at<?)",
                    (cutoff,),
                ).rowcount
                jobs = db.execute(
                    "DELETE FROM jobs WHERE status IN ('done','failed','cancelled') AND updated_at<?",
                    (cutoff,),
                ).rowcount
                return events, jobs
        finally:
            db.close()

    def _delete_memories(self, path: Path, cutoff: str) -> int:
        """Delete expired memories without orphaning their embedding vectors.

        The FK ON DELETE CASCADE only fires when the connection enables foreign
        keys, so do both explicitly in one transaction.
        """
        if not path.exists():
            return 0
        db = sqlite3.connect(path, timeout=5)
        try:
            with db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id IN (SELECT id FROM memories WHERE created_at<?)",
                    (cutoff,),
                )
                return db.execute("DELETE FROM memories WHERE created_at<?", (cutoff,)).rowcount
        finally:
            db.close()

    def _delete_webhooks(self, path: Path, status: str, cutoff: str) -> int:
        """Delete terminal deliveries and their retained attempts atomically."""
        if not path.exists():
            return 0
        db = sqlite3.connect(path, timeout=5)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "DELETE FROM webhook_attempts WHERE delivery_id IN"
                    " (SELECT id FROM webhook_deliveries WHERE status=? AND created_at<?)",
                    (status, cutoff),
                )
                return db.execute("DELETE FROM webhook_deliveries WHERE status=? AND created_at<?", (status, cutoff)).rowcount
        finally:
            db.close()

    def run(self, now: datetime | None = None, jobs_days: int = 30, memory_days: int = 90, audit_days: int = 365, runs_days: int = 90) -> RetentionReport:
        if min(jobs_days, memory_days, audit_days, runs_days) < 1:
            raise ValueError("retention windows must be at least one day")
        now = now or datetime.now(timezone.utc)
        jobs_cutoff = (now - timedelta(days=jobs_days)).isoformat()
        memory_cutoff = (now - timedelta(days=memory_days)).isoformat()
        runs_cutoff = (now - timedelta(days=runs_days)).isoformat()
        _audit_cutoff = (now - timedelta(days=audit_days)).isoformat()
        job_events, terminal_jobs = self._delete_jobs(
            self.data_dir / "jobs.sqlite3", jobs_cutoff
        )
        memories = self._delete_memories(self.data_dir / "meemee.sqlite3", memory_cutoff)
        runs = self.delete(self.data_dir / "runs.sqlite3", "DELETE FROM runs WHERE created_at<?", (runs_cutoff,))
        # Tamper-evident audit chains are intentionally retained; pruning needs signed anchors.
        audit_entries = 0
        webhook_delivered = self._delete_webhooks(self.data_dir / "webhooks.sqlite3", "delivered", jobs_cutoff)
        webhook_failed = self._delete_webhooks(self.data_dir / "webhooks.sqlite3", "failed", _audit_cutoff)
        idempotency = self.delete(self.data_dir / "idempotency.sqlite3", "DELETE FROM idempotency WHERE expires_at<=?", (now.isoformat(),))
        rate_limits = self.delete(self.data_dir / "rate-limits.sqlite3", "DELETE FROM rate_limits WHERE window_start<?", (int(now.timestamp()) - 86400,))
        return RetentionReport(job_events, terminal_jobs, memories, runs, audit_entries, idempotency, rate_limits, webhook_delivered, webhook_failed)

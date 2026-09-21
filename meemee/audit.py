from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    """Append-only tamper-evident audit chain for commercial operations."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS audit_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL, action TEXT NOT NULL, resource TEXT NOT NULL,
                    outcome TEXT NOT NULL, metadata TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, entry_hash TEXT NOT NULL UNIQUE
                );
            """)

    @staticmethod
    def hash(previous: str, occurred: str, actor: str, action: str, resource: str, outcome: str, metadata: str) -> str:
        canonical = f"{previous}\x1f{occurred}\x1f{actor}\x1f{action}\x1f{resource}\x1f{outcome}\x1f{metadata}"
        return hashlib.sha256(canonical.encode()).hexdigest()

    def append(self, actor: str, action: str, resource: str, outcome: str, metadata: dict[str, Any] | None = None) -> int:
        occurred = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            previous_row = self.db.execute("SELECT entry_hash FROM audit_log ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = previous_row[0] if previous_row else "0" * 64
            digest = self.hash(previous, occurred, actor, action, resource, outcome, encoded)
            cursor = self.db.execute(
                "INSERT INTO audit_log(occurred_at,actor_id,action,resource,outcome,metadata,previous_hash,entry_hash) VALUES(?,?,?,?,?,?,?,?)",
                (occurred, actor, action, resource, outcome, encoded, previous, digest),
            )
            return int(cursor.lastrowid)

    def verify(self) -> tuple[bool, int | None]:
        previous = "0" * 64
        for row in self.db.execute("SELECT * FROM audit_log ORDER BY sequence"):
            digest = self.hash(previous, row["occurred_at"], row["actor_id"], row["action"], row["resource"], row["outcome"], row["metadata"])
            if row["previous_hash"] != previous or row["entry_hash"] != digest:
                return False, row["sequence"]
            previous = row["entry_hash"]
        return True, None

    def list(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM audit_log WHERE sequence>? ORDER BY sequence LIMIT ?", (after, min(limit, 500))).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

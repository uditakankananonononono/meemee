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
        self.db.execute("PRAGMA busy_timeout=5000")
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
                CREATE TABLE IF NOT EXISTS audit_chain_base (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    sequence INTEGER NOT NULL, entry_hash TEXT NOT NULL
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
            if previous_row:
                previous = previous_row[0]
            else:
                base = self.db.execute("SELECT entry_hash FROM audit_chain_base WHERE singleton=1").fetchone()
                previous = base[0] if base else "0" * 64
            digest = self.hash(previous, occurred, actor, action, resource, outcome, encoded)
            cursor = self.db.execute(
                "INSERT INTO audit_log(occurred_at,actor_id,action,resource,outcome,metadata,previous_hash,entry_hash) VALUES(?,?,?,?,?,?,?,?)",
                (occurred, actor, action, resource, outcome, encoded, previous, digest),
            )
            return int(cursor.lastrowid)

    def verify(self) -> tuple[bool, int | None]:
        with self.lock:
            base = self.db.execute("SELECT sequence,entry_hash FROM audit_chain_base WHERE singleton=1").fetchone()
            previous = base["entry_hash"] if base else "0" * 64
            expected_sequence = int(base["sequence"]) + 1 if base else 1
            for row in self.db.execute("SELECT * FROM audit_log ORDER BY sequence"):
                if row["sequence"] != expected_sequence:
                    return False, expected_sequence
                digest = self.hash(previous, row["occurred_at"], row["actor_id"], row["action"], row["resource"], row["outcome"], row["metadata"])
                if row["previous_hash"] != previous or row["entry_hash"] != digest:
                    return False, row["sequence"]
                previous = row["entry_hash"]
                expected_sequence += 1
            return True, None

    def list_page(self, after: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], str | None]:
        page_size = min(max(limit, 1), 500)
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM audit_log WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, page_size + 1),
            ).fetchall()
        items = [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows[:page_size]]
        return items, str(items[-1]["sequence"]) if len(rows) > page_size else None

    def list(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return self.list_page(after, limit)[0]

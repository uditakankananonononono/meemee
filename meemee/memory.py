from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MemoryStore:
    """Durable SQLite event memory with full-text retrieval."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.connection:
            self.connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content, content='memories', content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                END;
            """)

    def add(self, run_id: str, kind: str, content: str, metadata: dict[str, Any] | None = None) -> int:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO memories(run_id, kind, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, kind, content, json.dumps(metadata or {}), datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        safe = " OR ".join(f'"{part}"' for part in query.split() if part) or '""'
        with self.lock:
            rows = self.connection.execute(
                """SELECT m.*, bm25(memories_fts) AS score FROM memories_fts
                   JOIN memories m ON m.id = memories_fts.rowid
                   WHERE memories_fts MATCH ? ORDER BY score LIMIT ?""",
                (safe, limit),
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

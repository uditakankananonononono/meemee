from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .semantic_memory import HashingEmbedder, cosine
from .sensitive import scrub_text


class MemoryStore:
    """Durable SQLite event memory with full-text retrieval."""

    def __init__(self, path: Path, embedder: HashingEmbedder | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.embedder = embedder or HashingEmbedder()
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
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                    dimensions INTEGER NOT NULL, embedding TEXT NOT NULL
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
                (run_id, kind, scrub_text(content), json.dumps(metadata or {}), datetime.now(timezone.utc).isoformat()),
            )
            memory_id = int(cursor.lastrowid)
            embedding = self.embedder.embed(scrub_text(content))
            self.connection.execute(
                "INSERT INTO memory_embeddings(memory_id,dimensions,embedding) VALUES(?,?,?)",
                (memory_id, len(embedding), json.dumps(embedding, separators=(",", ":"))),
            )
            return memory_id

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

    def semantic_search(self, query: str, limit: int = 8, candidates: int = 200) -> list[dict[str, Any]]:
        """Rank recent memories by deterministic embedding cosine similarity."""
        target = self.embedder.embed(query)
        with self.lock:
            rows = self.connection.execute(
                """SELECT m.*,e.embedding FROM memories m JOIN memory_embeddings e ON e.memory_id=m.id
                   ORDER BY m.id DESC LIMIT ?""", (max(limit, candidates),)
            ).fetchall()
        ranked = sorted(
            ((cosine(target, json.loads(row["embedding"])), row) for row in rows),
            key=lambda item: (-item[0], -item[1]["id"]),
        )[:max(1, limit)]
        return [{**dict(row), "metadata": json.loads(row["metadata"]), "semantic_score": score}
                for score, row in ranked]

    def hybrid_search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Fuse lexical and semantic ranks with reciprocal-rank fusion."""
        lexical = self.search(query, max(limit * 3, 20))
        semantic = self.semantic_search(query, max(limit * 3, 20))
        by_id: dict[int, dict[str, Any]] = {}
        scores: dict[int, float] = {}
        for result_set in (lexical, semantic):
            for rank, row in enumerate(result_set, 1):
                ident = int(row["id"]); by_id[ident] = row
                scores[ident] = scores.get(ident, 0.0) + 1.0 / (60 + rank)
        ordered = sorted(by_id, key=lambda ident: (-scores[ident], -ident))[:limit]
        return [{**by_id[ident], "hybrid_score": scores[ident]} for ident in ordered]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

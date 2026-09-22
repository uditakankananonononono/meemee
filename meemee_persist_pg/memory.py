from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from meemee.sensitive import scrub_text

from ._db import Database


class MemoryStore:
    def __init__(self, db: Database): self.db = db
    def add(self, run_id: str, kind: str, content: str, metadata: dict[str, Any] | None = None) -> int:
        if not run_id or not kind or not content: raise ValueError("run_id, kind and content are required")
        with self.db.transaction() as c:
            row = c.execute("INSERT INTO meemee_memories(run_id,kind,content,metadata) VALUES(%s,%s,%s,%s) RETURNING id", (run_id,kind,scrub_text(content),Jsonb(metadata or {}))).fetchone()
            return int(row["id"])
    def search(self, query: str, limit: int = 8) -> list[dict[str,Any]]:
        if not query.strip(): return []
        with self.db.transaction() as c:
            rows=c.execute("""SELECT id,run_id,kind,content,metadata,created_at,
             ts_rank_cd(search,websearch_to_tsquery('simple',%s)) AS score FROM meemee_memories
             WHERE search @@ websearch_to_tsquery('simple',%s) ORDER BY score DESC,id DESC LIMIT %s""",(query,query,max(1,min(limit,100)))).fetchall()
            return list(rows)
    def recent(self, limit: int = 20) -> list[dict[str,Any]]:
        with self.db.transaction() as c: return list(c.execute("SELECT id,run_id,kind,content,metadata,created_at FROM meemee_memories ORDER BY id DESC LIMIT %s",(max(1,min(limit,500)),)).fetchall())

    def semantic_search(self, query: str, limit: int = 8) -> list[dict[str,Any]]:
        """Use PostgreSQL full-text rank as the safe fallback until pgvector is configured."""
        return self.search(query, limit)
    def hybrid_search(self, query: str, limit: int = 8) -> list[dict[str,Any]]:
        """Provide the agent retrieval contract on PostgreSQL without failing startup runs."""
        return self.search(query, limit)

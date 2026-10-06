from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from meemee.semantic_memory import Embedder, cosine, embedding_space
from meemee.sensitive import scrub_text

from ._db import Database


class MemoryStore:
    def __init__(self, db: Database, embedder: Embedder | None = None):
        self.db, self.embedder = db, embedder
    def add(self, run_id: str, kind: str, content: str, metadata: dict[str, Any] | None = None, *, owner_id: str) -> int:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        if not run_id or not kind or not content: raise ValueError("run_id, kind and content are required")
        with self.db.transaction() as c:
            row = c.execute("INSERT INTO meemee_memories(run_id,kind,content,metadata,owner_id) VALUES(%s,%s,%s,%s,%s) RETURNING id", (run_id,kind,scrub_text(content),Jsonb(metadata or {}),owner_id)).fetchone()
            ident = int(row["id"])
            if self.embedder:
                vector = self.embedder.embed(scrub_text(content))
                c.execute("INSERT INTO meemee_memory_embeddings(memory_id,space_id,dimensions,embedding) VALUES(%s,%s,%s,%s)",
                          (ident, embedding_space(self.embedder), len(vector), Jsonb(vector)))
            return ident
    def search(self, query: str, limit: int = 8, *, owner_id: str) -> list[dict[str,Any]]:
        if not query.strip(): return []
        with self.db.transaction() as c:
            rows=c.execute("""SELECT id,run_id,kind,content,metadata,created_at,
             ts_rank_cd(search,websearch_to_tsquery('simple',%s)) AS score FROM meemee_memories
             WHERE search @@ websearch_to_tsquery('simple',%s) AND owner_id=%s ORDER BY score DESC,id DESC LIMIT %s""",(query,query,owner_id,max(1,min(limit,100)))).fetchall()
            return list(rows)
    def recent(self, limit: int = 20, *, owner_id: str) -> list[dict[str,Any]]:
        with self.db.transaction() as c: return list(c.execute("SELECT id,run_id,kind,content,metadata,created_at FROM meemee_memories WHERE owner_id=%s ORDER BY id DESC LIMIT %s",(owner_id,max(1,min(limit,500)))).fetchall())

    def reindex(self, batch_size: int = 32) -> int:
        if self.embedder is None:
            raise RuntimeError("semantic encoder not configured; use lexical search or MEEMEE_EMBEDDING_MODEL_DIR")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        count, space = 0, embedding_space(self.embedder)
        while True:
            with self.db.transaction() as c:
                c.execute("SELECT pg_advisory_xact_lock(%s)", (6758712042962292,))
                rows = c.execute("""SELECT m.id,m.content FROM meemee_memories m
                    LEFT JOIN meemee_memory_embeddings e ON e.memory_id=m.id
                    WHERE e.memory_id IS NULL OR e.space_id != %s ORDER BY m.id LIMIT %s""",
                    (space, batch_size)).fetchall()
                if not rows:
                    return count
                texts = [r["content"] for r in rows]
                vectors = (self.embedder.embed_many(texts) if hasattr(self.embedder, "embed_many")
                           else [self.embedder.embed(text) for text in texts])
                for row, vector in zip(rows, vectors):
                    c.execute("""INSERT INTO meemee_memory_embeddings(memory_id,space_id,dimensions,embedding)
                        VALUES(%s,%s,%s,%s) ON CONFLICT(memory_id) DO UPDATE SET
                        space_id=EXCLUDED.space_id, dimensions=EXCLUDED.dimensions, embedding=EXCLUDED.embedding""",
                        (row["id"], space, len(vector), Jsonb(vector)))
                count += len(rows)

    def semantic_search(self, query: str, limit: int = 8, *, owner_id: str) -> list[dict[str,Any]]:
        """Exact cosine across persisted learned vectors, not a full-text alias."""
        if not query.strip() or limit <= 0:
            return []
        self.reindex()
        target = self.embedder.embed(query)
        with self.db.transaction() as c:
            rows = c.execute("""SELECT m.*, e.embedding FROM meemee_memories m
                JOIN meemee_memory_embeddings e ON m.id=e.memory_id WHERE e.space_id=%s AND m.owner_id=%s""",
                (embedding_space(self.embedder), owner_id)).fetchall()
        ranked = sorted(((cosine(target, row["embedding"]), row) for row in rows),
                        key=lambda item: (-item[0], -item[1]["id"]))[:limit]
        return [{**row, "semantic_score": score, "embedding_space": embedding_space(self.embedder)} for score, row in ranked]

    def hybrid_search(self, query: str, limit: int = 8, *, owner_id: str) -> list[dict[str,Any]]:
        """Explicit lexical-only mode without an encoder; otherwise rank fusion."""
        if limit <= 0 or not query.strip():
            return []
        lexical = self.search(query, max(limit * 3, 20), owner_id=owner_id)
        if self.embedder is None:
            return [{**row, "retrieval_mode": "lexical-only"} for row in lexical[:limit]]
        semantic = self.semantic_search(query, max(limit * 3, 20), owner_id=owner_id)
        rows, scores = {}, {}
        for results in (lexical, semantic):
            for rank, row in enumerate(results, 1):
                ident = row["id"]
                rows[ident] = row
                scores[ident] = scores.get(ident, 0.0) + 1.0 / (60 + rank)
        ordered = sorted(rows, key=lambda ident: (-scores[ident], -ident))[:limit]
        return [{**rows[ident], "hybrid_score": scores[ident], "retrieval_mode": "learned-hybrid"}
                for ident in ordered]

    def delete_runs(self, run_ids: list[str], *, owner_id: str) -> int:
        """Hard-delete one owner's memories written by the given runs (embeddings cascade)."""
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        ids = list(dict.fromkeys(run_ids))
        if not ids: return 0
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_memories WHERE run_id=ANY(%s) AND owner_id=%s", (ids, owner_id)).rowcount

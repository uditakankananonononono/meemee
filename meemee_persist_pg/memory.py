from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from meemee.semantic_memory import Embedder, HashingEmbedder, cosine, reciprocal_rank_fusion
from meemee.sensitive import scrub_text

from ._db import Database

_COLUMNS = "m.id,m.run_id,m.kind,m.content,m.metadata,m.created_at"


class EmbeddingIndexError(RuntimeError):
    """The stored vectors cannot serve a search with the configured embedder (missing, other model/version/dimensions)."""


class MemoryStore:
    """PostgreSQL event memory with hash-vector parity and backend-native lexical ranking.

    - `search`: lexical, any query token may match (OR), ranked by `ts_rank_cd` (SQLite ranks by bm25),
      ties newest id first.
    - `semantic_search`: deterministic hash-vector cosine (not learned semantics) over the newest
      `max(limit, candidates)` memories, ties newest id first. Vectors are stored as float8[] with
      model/version/dimensions and computed by the same `HashingEmbedder` as SQLite.
    - `hybrid_search`: the same reciprocal-rank fusion algorithm (k=60) as SQLite, applied
      to backend-native lexical ranks. Hybrid order and scores need not equal SQLite.
    A search that cannot be served honestly (index missing or built by another embedder) raises
    `EmbeddingIndexError`; call `backfill_embeddings()` to repair.
    """

    def __init__(self, db: Database, embedder: Embedder | None = None):
        self.db = db
        self.embedder = embedder or HashingEmbedder()
        for attr in ("model", "version", "dimensions"):
            if not getattr(self.embedder, attr, None):
                raise ValueError(f"embedder must define {attr}")

    @property
    def embedding_spec(self) -> tuple[str, int, int]:
        return (self.embedder.model, int(self.embedder.version), int(self.embedder.dimensions))

    def _vector(self, text: str) -> list[float]:
        vector = [float(v) for v in self.embedder.embed(text)]
        if len(vector) != self.embedder.dimensions:
            raise ValueError("embedder returned a vector that does not match its declared dimensions")
        return vector

    def _put_embedding(self, conn, memory_id: int, content: str) -> None:
        model, version, dims = self.embedding_spec
        conn.execute(
            """INSERT INTO meemee_memory_embeddings(memory_id,model,version,dimensions,embedding)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model, version=excluded.version,
                 dimensions=excluded.dimensions, embedding=excluded.embedding, created_at=clock_timestamp()""",
            (memory_id, model, version, dims, self._vector(content)))

    def add(self, run_id: str, kind: str, content: str, metadata: dict[str, Any] | None = None) -> int:
        if not run_id or not kind or not content: raise ValueError("run_id, kind and content are required")
        clean = scrub_text(content)
        vector_text = clean  # embed exactly what is stored, as SQLite does
        with self.db.transaction() as c:
            row = c.execute("INSERT INTO meemee_memories(run_id,kind,content,metadata) VALUES(%s,%s,%s,%s) RETURNING id",
                            (run_id, kind, clean, Jsonb(metadata or {}))).fetchone()
            memory_id = int(row["id"])
            self._put_embedding(c, memory_id, vector_text)
            return memory_id

    def backfill_embeddings(self, batch_size: int = 500) -> int:
        """(Re)build vectors for memories that have none or were built by another model/version/dimensions."""
        model, version, dims = self.embedding_spec
        done = 0
        while True:
            with self.db.transaction() as c:
                rows = c.execute(
                    """SELECT m.id,m.content FROM meemee_memories m
                       LEFT JOIN meemee_memory_embeddings e ON e.memory_id=m.id
                       WHERE e.memory_id IS NULL OR (e.model,e.version,e.dimensions) IS DISTINCT FROM (%s,%s,%s)
                       ORDER BY m.id LIMIT %s FOR UPDATE OF m SKIP LOCKED""",
                    (model, version, dims, max(1, batch_size))).fetchall()
                for row in rows:
                    self._put_embedding(c, int(row["id"]), row["content"])
            done += len(rows)
            if len(rows) < max(1, batch_size):
                return done

    def _lexical(self, query: str, limit: int, run_id: str | None) -> list[dict[str, Any]]:
        tokens = list(dict.fromkeys(part for part in query.split() if part))
        if not tokens: return []
        # One plainto_tsquery per whitespace token, OR-ed, so any term matches (SQLite: OR of quoted tokens).
        tsq = " || ".join(["plainto_tsquery('simple',%s)"] * len(tokens))
        with self.db.transaction() as c:
            rows = c.execute(f"""SELECT {_COLUMNS}, ts_rank_cd(m.search,q.tsq) AS score
                FROM meemee_memories m, (SELECT {tsq} AS tsq) q
                WHERE m.search @@ q.tsq AND (%s::text IS NULL OR m.run_id=%s)
                ORDER BY score DESC, m.id DESC LIMIT %s""", (*tokens, run_id, run_id, max(1, limit))).fetchall()
            return list(rows)

    def search(self, query: str, limit: int = 8, run_id: str | None = None) -> list[dict[str, Any]]:
        return self._lexical(query, limit, run_id)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.transaction() as c: return list(c.execute("SELECT id,run_id,kind,content,metadata,created_at FROM meemee_memories ORDER BY id DESC LIMIT %s",(max(1,min(limit,500)),)).fetchall())

    def semantic_search(self, query: str, limit: int = 8, candidates: int = 200,
                        run_id: str | None = None) -> list[dict[str, Any]]:
        """Rank the newest memories by hash-vector cosine similarity (lexical-feature overlap, not learned semantics)."""
        target = self._vector(query)
        model, version, dims = self.embedding_spec
        with self.db.transaction() as c:
            rows = c.execute(f"""SELECT {_COLUMNS}, e.model AS e_model, e.version AS e_version,
                       e.dimensions AS e_dims, e.embedding AS e_vec
                FROM (SELECT * FROM meemee_memories WHERE (%s::text IS NULL OR run_id=%s)
                      ORDER BY id DESC LIMIT %s) m
                LEFT JOIN meemee_memory_embeddings e ON e.memory_id=m.id ORDER BY m.id DESC""",
                (run_id, run_id, max(limit, candidates))).fetchall()
        bad = [r["id"] for r in rows if r["e_vec"] is None or (r["e_model"], r["e_version"], r["e_dims"]) != (model, version, dims)]
        if bad:
            raise EmbeddingIndexError(
                f"{len(bad)} candidate memories (e.g. id {bad[0]}) have no vector for {model} v{version} d{dims}; "
                "run MemoryStore.backfill_embeddings()")
        ranked = sorted(((cosine(target, r["e_vec"]), r) for r in rows), key=lambda item: (-item[0], -item[1]["id"]))[:max(1, limit)]
        drop = {"e_model", "e_version", "e_dims", "e_vec"}
        return [{**{k: v for k, v in r.items() if k not in drop}, "semantic_score": score} for score, r in ranked]

    def hybrid_search(self, query: str, limit: int = 8, run_id: str | None = None) -> list[dict[str, Any]]:
        """RRF (k=60) of native lexical and hash-vector ranks; not SQLite ranking parity."""
        lexical = self._lexical(query, max(limit * 3, 20), run_id)
        semantic = self.semantic_search(query, max(limit * 3, 20), run_id=run_id)
        return reciprocal_rank_fusion(lexical, semantic, limit=limit)

    def delete_runs(self, run_ids: list[str]) -> int:
        """Hard-delete memories (and their vectors, by cascade) written by the given runs."""
        ids = list(dict.fromkeys(run_ids))
        if not ids: return 0
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_memories WHERE run_id=ANY(%s)", (ids,)).rowcount

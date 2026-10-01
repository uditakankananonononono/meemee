-- Hash-vector index for PostgreSQL memory search (same vectors as the SQLite store).
-- Vectors are float8[] computed by the application embedder; nothing here is pgvector or learned.
-- Existing rows are filled by MemoryStore.backfill_embeddings() (run automatically by build_persistence).
CREATE TABLE meemee_memory_embeddings (
 memory_id bigint PRIMARY KEY REFERENCES meemee_memories(id) ON DELETE CASCADE,
 model text NOT NULL CHECK(length(model) > 0),
 version integer NOT NULL CHECK(version > 0),
 dimensions integer NOT NULL CHECK(dimensions >= 32),
 embedding double precision[] NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(cardinality(embedding) = dimensions));
CREATE INDEX meemee_memory_embeddings_spec ON meemee_memory_embeddings(model, version, dimensions);

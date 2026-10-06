CREATE TABLE meemee_memory_embeddings (
    memory_id bigint PRIMARY KEY REFERENCES meemee_memories(id) ON DELETE CASCADE,
    space_id text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    embedding jsonb NOT NULL
);
CREATE INDEX meemee_memory_embeddings_space ON meemee_memory_embeddings(space_id);

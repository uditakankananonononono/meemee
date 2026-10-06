# Local trained semantic memory

October 7, 2026: this repair addresses two specific overclaims, not Meemee's full spec.
SQLite's earlier "semantic" encoder was lexical feature hashing. PostgreSQL's earlier
semantic method was an alias for full-text search. Neither reproduced paraphrase understanding.

## Implementation and provenance

`MiniLMEmbedder` loads the trained `sentence-transformers/all-MiniLM-L6-v2` encoder
at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Tokenizer and ONNX model
are SHA256-checked before load. ONNX Runtime runs on CPU, with no remote code or API.
The tokenizer truncates at 256 wordpieces. Attention-mask mean pooling followed by
L2 normalization produces 384 dimensions. Empty text, missing/corrupt weights and
non-finite outputs raise errors, never substitute feature hashing.

Sources:
- https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/README.md
- https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/onnx/model.onnx

## Setup

```sh
pip install '.[embeddings]'
meemee models pull-embeddings ./models/minilm
export MEEMEE_EMBEDDING_MODEL_DIR="$PWD/models/minilm"
```

Downloading free public weights is a separate explicit command. The running API/worker
only loads existing local files. With this setting, `persistence_from_settings` selects
the same encoder for SQLite and PostgreSQL. PostgreSQL schema migration 016 stores
vectors as JSONB and cascades their deletion with memories. No pgvector is required.

Both backends store a model-space identifier. On retrieval, absent or mismatched
vectors are re-encoded in bounded batches, so legacy hashes are never compared with
learned vectors. Interrupted reindexing is resumable. PostgreSQL uses a transaction
advisory lock to serialize reindex batches. Exact cosine scans all stored vectors;
it does not silently exclude memories older than the latest 200.

## Reproduced behavior

`tests/test_learned_memory.py` uses actual pinned pretrained weights, not mocked
embeddings. Tested on local CPU and a real PostgreSQL server:

- "My automobile needs fixing" vs "The car requires repairs": cosine 0.80128.
- Same first sentence vs "The beach sand is warm": cosine 0.05240.
- SQLite retrieves the first memory after 215 newer distractors, although full-text
  search for "car requires repairs" returns no matches.
- PostgreSQL persists, reopens, retrieves the same paraphrase, and deletes vectors.
- Reindexing 216 legacy hash rows replaces their vector space and is idempotent.
- Batch padding does not change a sentence's embedding.
- Missing/tampered weights fail closed; cosine rejects mixed dimensions and NaN.
- Runtime settings select the trained encoder, not merely a standalone demo.

Run:

```sh
MEEMEE_TEST_EMBEDDING_DIR=/path/to/pulled/minilm \
MEEMEE_TEST_POSTGRES_DSN=postgresql://your-local-test-server/postgres \
pytest -q tests/test_learned_memory.py tests/test_semantic_memory.py \
  tests/test_pg_memory_contract.py tests/test_persistence_selection.py
```

Do not label a skipped real-weight test as reproduced. No private owner data is used
in these tests. The paraphrase pair is a narrow regression test, not a retrieval benchmark.

## Limits and explicit modes

Without configured learned weights, SQLite still defaults to lexical feature hashing
for compatibility; PostgreSQL hybrid retrieval says `retrieval_mode=lexical-only`.
PostgreSQL `semantic_search` raises when no encoder is installed, rather than disguising
full-text search as semantics. Hybrid rank fusion can favor generic full-text matches,
so it is not a relevance guarantee. This model is primarily English short-text retrieval.
It does not reason, chat, copy the owner's mind, train itself, or make Meemee AGI.
The scan costs O(number of memories * 384) and loads vectors into memory. Large-scale
approximate indexing and multilingual evaluation are not implemented in this repair.

## Owner isolation repair

An additional source read found that Agent.run's `owner_id` was not passed to event
memory: one account's prior events could enter another account's model prompt. Memory
now stores a separate owner identifier and filters lexical, vector, hybrid and recent
retrieval before ranking. The agent passes the authenticated owner for every memory
write and read. Delegated children inherit the owner through the tool registry and
team executor; children that cannot accept an owner are refused for non-default owners.

SQLite upgrades existing memory rows into `default`. PostgreSQL migration 017 does the
same. Unknown legacy ownership is not guessed from metadata or disclosed to every
account. Such rows are only accessible from the local default identity; no retrospective
claim of isolation is made. Explicit tests use identical text under Alice/Bob/default
and verify both classic and real trained semantic retrieval, model-prompt canaries,
and delegated child writes. The CLI composition root now honors the configured encoder
too, rather than bypassing the settings-aware persistence constructor.

This is a repair to the event-memory boundary, not an audit of every product data path.

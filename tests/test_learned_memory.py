"""Real pinned weights: no fake sessions, constant encoders, or network in retrieval."""
import os
from pathlib import Path

import pytest

from meemee.memory import MemoryStore
from meemee.semantic_memory import HashingEmbedder, MiniLMEmbedder, cosine


@pytest.fixture(scope="module")
def encoder():
    path = os.getenv("MEEMEE_TEST_EMBEDDING_DIR")
    if not path:
        pytest.skip("set MEEMEE_TEST_EMBEDDING_DIR to pinned pulled MiniLM weights")
    return MiniLMEmbedder(Path(path))


def test_trained_encoder_understands_zero_shared_content_word_paraphrases(encoder):
    vectors = encoder.embed_many(["My automobile needs fixing", "The car requires repairs",
                                 "The beach sand is warm"])
    assert len(vectors[0]) == 384
    assert cosine(vectors[0], vectors[1]) > 0.7
    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2]) + 0.5
    assert abs(sum(v * v for v in vectors[0]) - 1) < 1e-6
    assert encoder.embed("My automobile needs fixing") == pytest.approx(vectors[0], abs=1e-6)


def test_masked_pooling_is_invariant_to_batch_padding_and_rejects_empty(encoder):
    text = "The car requires repairs"
    single = encoder.embed(text)
    batched = encoder.embed_many([text, "This is an unrelated sentence " * 30])[0]
    assert single == pytest.approx(batched, abs=1e-6)
    with pytest.raises(ValueError, match="non-empty"):
        encoder.embed(" ")


def test_cosine_refuses_mixed_dimensions_and_nonfinite():
    with pytest.raises(ValueError, match="dimensions"):
        cosine([1], [1, 2])
    with pytest.raises(ValueError, match="finite"):
        cosine([float("nan")], [1])


def test_missing_or_tampered_weights_never_silently_fall_back(tmp_path):
    pytest.importorskip("onnxruntime")
    with pytest.raises(FileNotFoundError):
        MiniLMEmbedder(tmp_path)
    (tmp_path / "tokenizer.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        MiniLMEmbedder(tmp_path)


def test_trained_encoder_needs_no_network_after_explicit_pull(encoder):
    import socket
    from unittest.mock import patch
    with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
        offline = MiniLMEmbedder(Path(os.environ["MEEMEE_TEST_EMBEDDING_DIR"]))
        assert len(offline.embed("The car requires repairs")) == 384


def test_sqlite_reindexes_hash_space_and_finds_old_paraphrase_memory(tmp_path, encoder):
    path = tmp_path / "memory.db"
    old = MemoryStore(path, HashingEmbedder())
    ident = old.add("r", "fact", "My automobile needs fixing")
    for _ in range(215):
        old.add("r", "fact", "The beach sand is warm")
    old.connection.close()
    store = MemoryStore(path, encoder)
    assert store.search("car requires repairs") == []
    assert store.reindex(batch_size=17) == 216
    assert store.reindex() == 0
    hit = store.semantic_search("The car requires repairs", limit=1)[0]
    assert hit["id"] == ident and hit["semantic_score"] > 0.7
    assert store.hybrid_search("car requires repairs", limit=1)[0]["id"] == ident
    assert store.semantic_search("", 1) == []
    assert store.hybrid_search("car", 0) == []
    assert store.delete_runs(["r"]) == 216
    assert store.connection.execute("SELECT count(*) FROM memory_embeddings").fetchone()[0] == 0


def test_settings_wire_real_encoder_into_sqlite_runtime(tmp_path, encoder):
    from meemee.config import Settings
    from meemee.persistence import persistence_from_settings
    settings = Settings(data_dir=tmp_path, embedding_model_dir=Path(os.environ["MEEMEE_TEST_EMBEDDING_DIR"]))
    persistence = persistence_from_settings(settings)
    assert persistence.memory.embedder.space_id == encoder.space_id
    ident = persistence.memory.add("r", "fact", "My automobile needs fixing")
    assert persistence.memory.semantic_search("The car requires repairs", 1)[0]["id"] == ident


def test_real_postgres_persisted_paraphrase_vectors_reindex_delete(encoder):
    import uuid

    import psycopg
    from psycopg.conninfo import make_conninfo

    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.memory import MemoryStore as PGMemory
    dsn = os.getenv("MEEMEE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires real PostgreSQL")
    name = "learned_memory_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    db = Database(make_conninfo(dsn, dbname=name))
    try:
        MigrationStore(db).apply()
        lexical = PGMemory(db)
        ident = lexical.add("r", "fact", "My automobile needs fixing")
        store = PGMemory(db, encoder)
        assert store.reindex(batch_size=1) == 1
        store.add("r", "fact", "The beach sand is warm")
        assert store.search("car requires repairs") == []
        assert store.semantic_search("The car requires repairs", 1)[0]["id"] == ident
        assert store.hybrid_search("The car requires repairs", 1)[0]["retrieval_mode"] == "learned-hybrid"
        reopened = PGMemory(db, encoder)
        assert reopened.semantic_search("The car requires repairs", 1)[0]["id"] == ident
        assert reopened.reindex() == 0
        assert store.delete_runs(["r"]) == 2
        with db.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM meemee_memory_embeddings").fetchone()["n"] == 0
    finally:
        db.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')

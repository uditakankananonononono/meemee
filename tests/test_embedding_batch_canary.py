"""Batch encoders must not silently omit or corrupt persisted rows."""
import pytest

from meemee.memory import MemoryStore
from meemee.semantic_memory import HashingEmbedder


@pytest.mark.parametrize('mode', ['short', 'nan', 'dimension'])
def test_invalid_encoder_batch_fails_without_partial_write(tmp_path, mode):
    store = MemoryStore(tmp_path / 'm.db', HashingEmbedder(32))
    store.add('r', 'goal', 'fixture one', owner_id='o')
    store.add('r', 'goal', 'fixture two', owner_id='o')

    class Broken:
        dimensions = 32
        space_id = 'fixture-broken'

        def embed_many(self, texts):
            if mode == 'short':
                return [[1.0] * 32]
            if mode == 'nan':
                return [[float('nan')] * 32 for _ in texts]
            return [[1.0] for _ in texts]

    store.embedder = Broken()
    with pytest.raises(ValueError):
        store.reindex()
    assert store.connection.execute("SELECT count(*) FROM memory_embeddings WHERE space_id='fixture-broken'").fetchone()[0] == 0

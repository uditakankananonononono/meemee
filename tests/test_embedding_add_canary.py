"""Invalid single encoder output must not commit memory or embedding rows."""
import pytest

from meemee.memory import MemoryStore


@pytest.mark.parametrize('vector', [[float('nan')] * 32, [1.0], [True] * 32])
def test_invalid_single_embedding_rolls_back_add(tmp_path, vector):
    class Broken:
        dimensions = 32
        space_id = 'fixture-invalid'

        def embed(self, text):
            return vector

    store = MemoryStore(tmp_path / 'm.db', Broken())
    with pytest.raises(ValueError):
        store.add('run', 'fact', 'invalidembedding fixture', owner_id='o')
    assert store.recent(owner_id='o') == []
    assert store.connection.execute('SELECT count(*) FROM memory_embeddings').fetchone()[0] == 0
    assert store.search('invalidembedding', owner_id='o') == []

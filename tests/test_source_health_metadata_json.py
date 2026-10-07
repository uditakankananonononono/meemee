"""Source health metadata must remain interoperable finite JSON."""

import pytest

from meemee.source_health import SourceHealthStore


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_source_health_rejects_nonfinite_metadata_before_publication(tmp_path, value):
    store = SourceHealthStore(tmp_path / 'health.sqlite3')
    with pytest.raises(ValueError):
        store.record('owner', 'source', ok=True, metadata={'nested': [value]})
    assert store.get('owner', 'source') is None
    assert store.history('owner', 'source') == []

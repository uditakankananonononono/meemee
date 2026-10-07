"""Recorded latency is a finite duration, not NaN or infinity."""

import pytest

from meemee.source_health import SourceHealthStore


@pytest.mark.parametrize('latency', [float('nan'), float('inf'), float('-inf')])
def test_source_health_rejects_nonfinite_latency_without_publication(tmp_path, latency):
    store = SourceHealthStore(tmp_path / 'health.sqlite3')
    with pytest.raises(ValueError, match='latency'):
        store.record('owner', 'source', ok=True, latency_ms=latency)
    assert store.get('owner', 'source') is None
    assert store.history('owner', 'source') == []

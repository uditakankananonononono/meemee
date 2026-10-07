"""Only an explicit boolean is a source check outcome."""

import pytest

from meemee.source_health import SourceHealthStore


@pytest.mark.parametrize('outcome', [1, 0, 'false', None])
def test_source_check_rejects_nonboolean_outcome(tmp_path, outcome):
    store = SourceHealthStore(tmp_path / 'health.sqlite3')
    with pytest.raises((ValueError, TypeError)):
        store.record('owner', 'source', ok=outcome)
    assert store.get('owner', 'source') is None
    assert store.history('owner', 'source') == []

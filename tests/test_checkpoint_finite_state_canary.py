"""Resumable state must serialize as interoperable finite JSON."""
import pytest

from meemee.checkpoints import CheckpointStore


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_nonfinite_checkpoint_refused_before_publication(tmp_path, value):
    store = CheckpointStore(tmp_path / 'c.db')
    with pytest.raises(ValueError):
        store.save('owner', 'run', 'goal', 'step', {'nested': {'value': value}})
    assert store.latest('owner', 'run') is None

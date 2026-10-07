"""Stored checkpoint corruption must not become resumed execution state."""
import pytest

from meemee.checkpoints import CheckpointStore


@pytest.mark.parametrize('read', ['latest', 'get', 'history', 'resume'])
def test_corrupt_checkpoint_rejected_on_read(tmp_path, read):
    store = CheckpointStore(tmp_path / 'c.db')
    store.save('owner', 'run', 'goal', 'planned', {'safe': True})
    store.db.execute("UPDATE agency_checkpoints SET state=?", ('{"safe":false}',))
    with pytest.raises(ValueError, match='checksum'):
        if read == 'get':
            store.get('owner', 'run', 1)
        else:
            getattr(store, read)('owner', 'run')

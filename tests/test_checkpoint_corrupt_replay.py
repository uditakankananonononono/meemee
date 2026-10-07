"""Idempotent checkpoint reads preserve corruption errors and connection health."""

import pytest

from meemee.checkpoints import CheckpointStore


def test_corrupt_idempotent_replay_preserves_checksum_error(tmp_path):
    store = CheckpointStore(tmp_path / 'checkpoints.sqlite3')
    saved = store.save('owner', 'run', 'goal', 'plan', {'step': 1}, idempotency_key='same')
    store.db.execute('UPDATE agency_checkpoints SET state=? WHERE id=?', ('{"step":2}', saved['id']))
    with pytest.raises(ValueError, match='checksum'):
        store.save('owner', 'run', 'goal', 'plan', {'step': 1}, idempotency_key='same')
    assert not store.db.in_transaction
    next_row = store.save('owner', 'run', 'goal', 'work', {'step': 3})
    assert next_row['sequence'] == 2

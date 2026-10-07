"""Publication must not rewrite a key reserved for a different request digest."""
import pytest

from meemee.idempotency import IdempotencyConflict, IdempotencyStore


@pytest.mark.parametrize('completed', [False, True])
def test_different_payload_cannot_publish_over_reserved_digest(tmp_path, completed):
    store = IdempotencyStore(tmp_path / 'i.db')
    original = {'goal': 'original'}
    store.claim('owner', '/jobs', 'key', original)
    if completed:
        store.put('owner', '/jobs', 'key', original, 201, {'id': 'original'})
    with pytest.raises(IdempotencyConflict):
        store.put('owner', '/jobs', 'key', {'goal': 'different'}, 201, {'id': 'wrong'})
    row = store.db.execute('SELECT * FROM idempotency').fetchone()
    assert row['request_hash'] == store.request_hash(original)
    assert row['status'] == (201 if completed else 0)

"""Unfixed expired same-digest ownership defects, outside root collection."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.idempotency import IdempotencyInProgress


def expire_claim(backend):
    store = backend.idempotency
    if backend.backend == 'sqlite':
        with store.db:
            store.db.execute("UPDATE idempotency SET expires_at='2000-01-01T00:00:00+00:00'")
    else:
        with store.db.transaction() as c:
            c.execute("UPDATE meemee_idempotency SET expires_at='2000-01-01T00:00:00+00:00'")


@pytest.mark.parametrize('stale_action', ['release', 'put'])
def test_stale_claimant_cannot_change_replacement(persistence, stale_action):  # noqa: F811
    store = persistence.idempotency
    payload = {'goal': 'same'}
    assert store.claim('owner', '/jobs', 'key', payload) is None
    expire_claim(persistence)
    assert store.claim('owner', '/jobs', 'key', payload) is None
    if stale_action == 'release':
        store.release('owner', '/jobs', 'key')
    else:
        store.put('owner', '/jobs', 'key', payload, 200, {'id': 'stale-job'})
    with pytest.raises(IdempotencyInProgress):
        store.get('owner', '/jobs', 'key', payload)

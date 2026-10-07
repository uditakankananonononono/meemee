"""A completed response cannot publish the pending sentinel as its HTTP status."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.idempotency import IdempotencyInProgress


def test_pending_sentinel_cannot_be_published_response(persistence):  # noqa: F811
    store = persistence.idempotency
    assert store.claim('owner', '/route', 'key', {}) is None
    with pytest.raises(ValueError, match='status'):
        store.put('owner', '/route', 'key', {}, 0, {'done': True})
    with pytest.raises(IdempotencyInProgress):
        store.get('owner', '/route', 'key', {})
    store.put('owner', '/route', 'key', {}, 200, {'done': True})
    assert store.get('owner', '/route', 'key', {}) == (200, {'done': True})

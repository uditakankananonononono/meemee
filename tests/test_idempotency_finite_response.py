"""Non-JSON response numbers cannot become cached responses."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.idempotency import IdempotencyInProgress


def test_nonfinite_response_rejected_without_consuming_claim(persistence):  # noqa: F811
    store = persistence.idempotency
    store.claim('owner', '/route', 'key', {})
    with pytest.raises(ValueError):
        store.put('owner', '/route', 'key', {}, 200, {'value': float('nan')})
    with pytest.raises(IdempotencyInProgress):
        store.get('owner', '/route', 'key', {})
    store.put('owner', '/route', 'key', {}, 200, {'value': 1})
    assert store.get('owner', '/route', 'key', {}) == (200, {'value': 1})

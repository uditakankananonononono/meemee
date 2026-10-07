"""Nonstandard JSON requests must not create durable deduplication identities."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401


@pytest.mark.parametrize('operation', ['claim', 'get', 'put'])
def test_idempotency_rejects_nonfinite_request(persistence, operation):  # noqa: F811
    store = persistence.idempotency
    payload = {'value': float('nan')}
    with pytest.raises(ValueError, match='JSON'):
        if operation == 'put':
            store.put('owner', '/route', 'key', payload, 200, {'ok': True})
        else:
            getattr(store, operation)('owner', '/route', 'key', payload)
    assert store.get('owner', '/route', 'key', {'value': 1}) is None

"""Webhook outboxes must not publish nonstandard JSON numbers."""

import pytest
from test_webhook_backends import _no_dns, backend, store  # noqa: F401


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_webhook_nonfinite_payload_rejected_without_outbox_row(store, value):  # noqa: F811
    store.subscribe('owner', 'https://hooks.example/a', {'*'})
    with pytest.raises(ValueError):
        store.enqueue('event', 'job.done', {'nested': [value]}, principal='owner')
    assert store.list_deliveries('owner')[0] == []
    assert store.enqueue('finite', 'job.done', {'nested': [1.5]}, principal='owner') == 1

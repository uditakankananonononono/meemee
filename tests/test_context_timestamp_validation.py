"""Bad event timestamps must not poison owner context or advance source cursor."""

import pytest
from test_personal_context_backends import stores  # noqa: F401

from meemee.context import ContextRecord


@pytest.mark.parametrize('stamp', ['not-a-date', '2026-02-30T00:00:00Z'])
def test_bad_context_timestamp_rejected_before_publish(stores, stamp):  # noqa: F811
    _, store = stores()
    store.register_source('owner', 'source', 'test', {})
    store.ingest(ContextRecord('owner', 'source', 'valid', 'event', 'title', 'fixture',
                               '2026-10-08', {}, cursor='valid-cursor'))
    with pytest.raises(ValueError):
        store.ingest(ContextRecord('owner', 'source', 'bad', 'event', 'title', 'fixture',
                                   stamp, {}, cursor='bad-cursor'))
    assert store.cursor('owner', 'source') == 'valid-cursor'
    assert [r['external_id'] for r in store.recent('owner')] == ['valid']
    assert [r['external_id'] for r in store.search('owner', 'fixture')] == ['valid']

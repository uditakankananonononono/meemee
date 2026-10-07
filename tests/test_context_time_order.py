"""Context readers compare timestamp instants while preserving original values."""

import pytest

from meemee.context import ContextRecord, ContextStore


@pytest.fixture
def store(tmp_path):
    store = ContextStore(tmp_path / 'context.sqlite3')
    store.register_source('owner', 'source', 'test', {})
    for ident, stamp in [('early', '2026-10-07T15:30:00+05:30'),
                         ('later', '2026-10-07T11:00:00+00:00')]:
        store.ingest(ContextRecord('owner', 'source', ident, 'event', 'same', 'fixture', stamp, {}))
    return store


def test_recent_orders_actual_instant_before_limit(store):
    assert [r['external_id'] for r in store.recent('owner', limit=1)] == ['later']


def test_search_tie_orders_actual_instant_before_limit(store):
    assert [r['external_id'] for r in store.search('owner', 'fixture', limit=1)] == ['later']


def test_date_only_and_equal_instant_tiebreak_preserve_values(store):
    for ident, stamp in [('date', '2026-10-08'), ('utc', '2026-10-08T00:00:00Z')]:
        store.ingest(ContextRecord('owner', 'source', ident, 'event', 'same', 'fixture', stamp, {}))
    for records in [store.recent('owner'), store.search('owner', 'fixture')]:
        assert [r['external_id'] for r in records[:2]] == ['utc', 'date']
        assert records[1]['occurred_at'] == '2026-10-08'
    assert store.recent('other') == []

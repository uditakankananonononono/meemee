"""Actual backend readers use instants, preserving timestamp spelling."""

import pytest
from test_personal_context_backends import stores  # noqa: F401

from meemee.context import ContextRecord


@pytest.mark.parametrize('reader', ['recent', 'search'])
def test_context_offset_latest_before_limit(stores, reader):  # noqa: F811 - imported pytest fixture
    _, store = stores()
    store.register_source('owner', 'source', 'test', {})
    for ident, stamp in [('early', '2026-10-07T15:30:00+05:30'),
                         ('later', '2026-10-07T11:00:00+00:00')]:
        store.ingest(ContextRecord('owner', 'source', ident, 'event', 'same', 'fixture', stamp, {}))
    records = store.recent('owner', limit=1) if reader == 'recent' else store.search('owner', 'fixture', limit=1)
    assert [r['external_id'] for r in records] == ['later']
    assert records[0]['occurred_at'] == '2026-10-07T11:00:00+00:00'


@pytest.mark.parametrize('reader', ['recent', 'search'])
def test_context_naive_date_ties_assume_utc_even_with_pg_session_timezone(stores, reader):  # noqa: F811 - imported pytest fixture
    _, store = stores()
    if not hasattr(store, 'lock'):
        # Alter pooled session default deliberately; reader must not inherit it.
        with store.db.transaction() as c:
            c.execute("SET TIME ZONE 'Asia/Kolkata'")
    store.register_source('owner', 'source', 'test', {})
    for ident, stamp in [('date', '2026-10-08'), ('naive', '2026-10-08T00:00:00'),
                         ('utc', '2026-10-08T00:00:00Z'), ('latest', '2026-10-08T00:01:00Z')]:
        store.ingest(ContextRecord('owner', 'source', ident, 'event', 'same', 'fixture', stamp, {}))
    records = store.recent('owner') if reader == 'recent' else store.search('owner', 'fixture')
    assert [r['external_id'] for r in records] == ['latest', 'utc', 'naive', 'date']
    assert records[-1]['occurred_at'] == '2026-10-08'

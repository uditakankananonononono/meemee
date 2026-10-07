"""Timeline bounds and ordering compare instants, not ISO string spellings."""

from meemee.context import ContextRecord, ContextStore
from meemee.timeline import Timeline


def setup_timeline(tmp_path):
    store = ContextStore(tmp_path / 'context.sqlite3')
    store.register_source('owner', 'calendar', 'test', {})
    for ident, stamp in [('early', '2026-10-07T15:30:00+05:30'),
                         ('later', '2026-10-07T11:00:00+00:00')]:
        store.ingest(ContextRecord('owner', 'calendar', ident, 'event', ident, 'fixture', stamp, {}))
    return Timeline(store)


def test_timeline_offset_range_filters_actual_instant(tmp_path):
    timeline = setup_timeline(tmp_path)
    items = timeline.range('owner', start='2026-10-07T09:59:00+00:00', end='2026-10-07T10:01:00+00:00')
    assert [item.title for item in items] == ['early']


def test_timeline_offset_order_and_pagination_use_actual_instant(tmp_path):
    timeline = setup_timeline(tmp_path)
    first = timeline.range('owner', limit=1)
    assert [item.title for item in first] == ['later']
    rest = timeline.range('owner', before=(first[0].occurred_at, first[0].id))
    assert [item.title for item in rest] == ['early']

"""An explicitly empty visibility allowance is not a default grant."""
import pytest

from meemee.context import ContextRecord, ContextStore


@pytest.mark.parametrize('method', ['recent', 'search'])
def test_empty_allowed_visibility_returns_no_records(tmp_path, method):
    context = ContextStore(tmp_path / 'c.db')
    context.register_source('o', 's', 'fixture', {})
    context.ingest(ContextRecord('o', 's', 'r', 'document', 'fixture', 'privatecanary', '2026-01-01T00:00:00Z', {}))
    args = ('o', 'privatecanary') if method == 'search' else ('o',)
    assert getattr(context, method)(*args, allowed=set()) == []

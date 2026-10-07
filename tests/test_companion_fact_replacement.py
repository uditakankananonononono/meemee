"""Fact corrections cannot point at missing or another owner's records."""

import pytest

from meemee.companion.models import FactInput
from meemee.companion.store import CompanionStore


@pytest.mark.parametrize('replacement_kind', ['missing', 'other_owner'])
def test_fact_replacement_preserves_source_on_invalid_target(tmp_path, replacement_kind):
    store = CompanionStore(tmp_path / 'companion.sqlite3')
    original = store.add_fact('owner', FactInput(text='Likes tea'), 'test')
    if replacement_kind == 'missing':
        target = original['id'] + 100
    else:
        target = store.add_fact('other', FactInput(text='Likes coffee'), 'test')['id']
    with pytest.raises(ValueError, match='replacement'):
        store.supersede_fact(original['id'], target)
    assert store.get_fact(original['id'])['superseded_by'] is None
    assert store.list_facts('owner')[0]['id'] == original['id']


def test_fact_replacement_keeps_valid_and_missing_source_contracts(tmp_path):
    store = CompanionStore(tmp_path / 'companion.sqlite3')
    first = store.add_fact('owner', FactInput(text='Likes tea'), 'test')
    second = store.add_fact('owner', FactInput(text='Likes coffee'), 'test')
    assert store.supersede_fact(first['id'], second['id'])
    assert store.get_fact(first['id'])['superseded_by'] == second['id']
    assert not store.supersede_fact(first['id'])
    assert not store.supersede_fact(900)
    assert store.supersede_fact(second['id'])
    assert store.get_fact(second['id'])['superseded_by'] == second['id']

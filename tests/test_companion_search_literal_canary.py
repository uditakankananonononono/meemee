"""User quotation marks must remain data, not FTS expression syntax."""
import pytest

from meemee.companion.models import FactInput
from meemee.companion.store import CompanionStore


@pytest.mark.parametrize('query', ['"Atlas"', '"Atlas', 'Atlas" OR "secret'])
def test_quotes_are_literal_search_input(tmp_path, query):
    store = CompanionStore(tmp_path / 'c.db')
    store.add_fact('owner', FactInput(text='Atlas platform'), source='fixture')
    store.add_fact('other', FactInput(text='secret'), source='fixture')
    hits = store.search_facts('owner', query)
    assert len(hits) == 1
    assert hits[0]['user_id'] == 'owner'

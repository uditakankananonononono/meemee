"""Literal user queries must not become malformed FTS syntax."""
import pytest

from meemee.memory import MemoryStore


@pytest.mark.parametrize('query', ['"', 'hello"world', '"hello"'])
def test_quotes_in_query_do_not_raise_sqlite_error(tmp_path, query):
    memory = MemoryStore(tmp_path / 'm.db')
    memory.add('r', 'goal', 'hello world', owner_id='o')
    result = memory.search(query, owner_id='o')
    assert isinstance(result, list)

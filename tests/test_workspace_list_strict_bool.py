import pytest

from meemee.tools.filesystem import ListArgs


@pytest.mark.parametrize('value',['false','true',0,1])
def test_hidden_toggle_refuses_coercion(value):
 with pytest.raises(ValueError):ListArgs(path='.',include_hidden=value)
def test_boolean_toggle_still_works():
 assert ListArgs(path='.',include_hidden=True).include_hidden is True
 assert ListArgs(path='.',include_hidden=False).include_hidden is False

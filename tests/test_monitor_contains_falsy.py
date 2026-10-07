"""Contains treats present false/zero values as values, not missing fields."""

import pytest
from test_monitors_reflection_backends import stores  # noqa: F401

from meemee.monitors import MonitorInput


@pytest.mark.parametrize('value, expected', [(0, '0'), (False, 'false')])
def test_contains_present_falsy_value_fires(stores, value, expected):  # noqa: F811
    # Both backend implementations share predicate evaluation.
    monitors, _ = stores()
    item = monitors.create('owner', MonitorInput(name='signal', source_id='source', field='value',
                                                operator='contains', expected=expected))
    assert monitors.evaluate('owner', 'source', {'value': value}) == [item['id']]

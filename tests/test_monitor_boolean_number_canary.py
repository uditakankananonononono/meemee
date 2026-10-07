"""Boolean payloads must not satisfy numeric monitor predicates."""
import pytest

from meemee.monitors import MonitorStore


@pytest.mark.parametrize(('operator', 'expected', 'value'), [
    ('eq', 1, True), ('eq', False, 0), ('gt', 0, True), ('gte', 1, True),
])
def test_monitor_booleans_are_not_numbers(operator, expected, value):
    assert not MonitorStore.matches({'field': 'price', 'operator': operator, 'expected': expected}, {'price': value})

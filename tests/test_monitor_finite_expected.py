"""Numeric monitor predicates must be valid finite JSON numbers."""

import pytest
from pydantic import ValidationError

from meemee.monitors import MonitorInput


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_monitor_rejects_nonfinite_expected(value):
    with pytest.raises(ValidationError, match='finite'):
        MonitorInput(name='price', source_id='shop', field='price', operator='lt', expected=value)

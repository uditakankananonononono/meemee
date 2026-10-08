"""A scheduler result must be standard JSON before its watermark is published."""

import pytest
from test_monitors_reflection_backends import stores  # noqa: F401


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_reflection_result_rejects_nonfinite_before_watermark(stores, value):  # noqa: F811
    _, schedule = stores()
    with pytest.raises(ValueError, match='JSON'):
        schedule.record('owner', 'ok', {'accepted': value}, 7)
    assert schedule.state('owner') is None
    schedule.record('owner', 'ok', {'accepted': 1}, 7)
    assert schedule.state('owner')['watermark'] == 7

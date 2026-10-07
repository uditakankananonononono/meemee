"""Invalid JSON event numbers must not spend a monitor's finite firing budget."""

import pytest
from test_monitors_reflection_backends import stores  # noqa: F401

from meemee.monitors import MonitorInput


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_monitor_rejects_nonfinite_event_before_fire(stores, value):  # noqa: F811
    monitors, _ = stores()
    item = monitors.create('owner', MonitorInput(name='present', source_id='source', field='value', operator='exists'))
    with pytest.raises(ValueError, match='JSON'):
        monitors.evaluate('owner', 'source', {'value': value})
    assert monitors.get('owner', item['id'])['fire_count'] == 0
    assert [row['kind'] for row in monitors.events('owner', item['id'])] == ['created']
    assert monitors.evaluate('owner', 'source', {'value': 0}) == [item['id']]

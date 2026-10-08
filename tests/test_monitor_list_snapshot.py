"""A monitor listing must not produce null rows when a concurrent purge removes IDs."""

from test_monitors_reflection_backends import stores  # noqa: F401

from meemee.monitors import MonitorInput


def test_monitor_list_uses_single_row_snapshot(stores, monkeypatch):  # noqa: F811
    monitors, _ = stores()
    row = monitors.create('owner', MonitorInput(name='watch', source_id='source', field='v', operator='exists'))
    original = monitors.get
    def deleted_before_fetch(owner, ident):
        monitors.delete_owner(owner)
        return original(owner, ident)
    monkeypatch.setattr(monitors, 'get', deleted_before_fetch)
    result = monitors.list('owner')
    assert all(isinstance(item, dict) for item in result)
    assert [item['id'] for item in result] == [row['id']]

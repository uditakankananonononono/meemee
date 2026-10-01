from concurrent.futures import ThreadPoolExecutor

from meemee.monitors import MonitorInput, MonitorStore


def test_durable_event_delivery_and_restart(tmp_path):
    path = tmp_path / 'monitors.db'
    store = MonitorStore(path)
    monitor = store.create('owner', MonitorInput(name='Ready', source_id='feed',
                            field='content', operator='contains', expected='ready', max_fires=2))
    store.accept_event('owner', 'feed', 'event-1', {'content': 'ready'})
    restarted = MonitorStore(path)
    assert restarted.dispatch() == 1
    assert restarted.dispatch() == 0
    assert len(restarted.notifications('owner')) == 1
    restarted.accept_event('owner', 'feed', 'event-1', {'content': 'ready'})
    assert restarted.get('owner', monitor['id'])['fire_count'] == 1
    assert restarted.notifications('other') == []


def test_competing_evaluators_cancel_and_deadline(tmp_path):
    path = tmp_path / 'm.db'
    first, second = MonitorStore(path), MonitorStore(path)
    monitor = first.create('o', MonitorInput(name='Ready', source_id='s', field='ok',
                          operator='eq', expected=True, max_fires=3))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda store: store.accept_event('o', 's', '1', {'ok': True}),
                      [first, second]))
    assert first.get('o', monitor['id'])['fire_count'] == 1
    assert first.cancel('o', monitor['id'])
    assert second.dispatch() == 0
    assert first.notifications('o') == []
    expired = first.create('o', MonitorInput(name='old', source_id='s', field='ok',
                         operator='exists', deadline='2000-01-01T00:00:00Z'))
    first.accept_event('o', 's', '2', {'ok': True})
    assert first.get('o', expired['id'])['status'] == 'timed_out'


def test_terminal_cancel_before_delivery_and_cross_owner(tmp_path):
    store = MonitorStore(tmp_path / 'm.db')
    monitor = store.create('o', MonitorInput(name='One', source_id='s', field='ok',
                          operator='eq', expected=True))
    store.accept_event('other', 's', '1', {'ok': True})
    assert store.get('o', monitor['id'])['fire_count'] == 0
    store.accept_event('o', 's', '1', {'ok': True})
    assert store.cancel('o', monitor['id'])
    assert store.dispatch() == 0

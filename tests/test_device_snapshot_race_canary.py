"""An execution snapshot must correspond to that execution, not a later one."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.device_protocol import make_command
from meemee.device_simulator import StatefulDeviceSimulator


def test_execution_snapshot_and_history_are_atomic(tmp_path, monkeypatch):
    def increment(state):
        state['n'] += 1
        return state['n']

    simulator = StatefulDeviceSimulator('d', '11' * 32, {'increment': increment}, {'n': 0})
    commands = [make_command('d', 'increment', {}, b'\x11' * 32) for _ in range(2)]
    first_snapshot = threading.Event()
    original = simulator.snapshot
    count = [0]

    def delayed_snapshot():
        count[0] += 1
        if count[0] == 1:
            first_snapshot.set()
            time.sleep(0.15)
        return original()

    monkeypatch.setattr(simulator, 'snapshot', delayed_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(simulator.execute, commands[0])
        assert first_snapshot.wait(2)
        second = pool.submit(simulator.execute, commands[1])
        results = [first.result(timeout=3), second.result(timeout=3)]
    assert results[0].result == 1 and results[0].state['n'] == 1
    assert results[1].result == 2 and results[1].state['n'] == 2
    assert [row.result for row in simulator.history] == [1, 2]

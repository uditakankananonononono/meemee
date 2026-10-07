"""Recreating a protocol peer must not execute the same command again."""
import pytest

from meemee.device_protocol import make_command
from meemee.devices import DeviceSimulator


def test_recreated_peer_rejects_previously_executed_command(tmp_path):
    calls = []
    envelope = make_command('d', 'echo', {}, b'\x11' * 32)
    first = DeviceSimulator('d', '11' * 32, {'echo': lambda: calls.append('effect')}, replay_path=tmp_path / 'replay.db')
    first.execute(envelope)
    first.replay.close()
    second = DeviceSimulator('d', '11' * 32, {'echo': lambda: calls.append('effect')}, replay_path=tmp_path / 'replay.db')
    with pytest.raises(ValueError, match='replayed'):
        second.execute(envelope)
    assert calls == ['effect']


def test_independent_durable_guards_claim_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from meemee.device_protocol import SQLiteReplayGuard

    guards = [SQLiteReplayGuard(tmp_path / 'r.db', 'peer'), SQLiteReplayGuard(tmp_path / 'r.db', 'peer')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda guard: guard.claim('nonce'), guards))
    assert sorted(results) == [False, True]
    assert SQLiteReplayGuard(tmp_path / 'r.db', 'other-peer').claim('nonce')

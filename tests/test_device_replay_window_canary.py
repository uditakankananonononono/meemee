"""Nonce retention must cover the signed envelope's full acceptance horizon."""
import pytest

from meemee.device_protocol import ReplayGuard, make_command, verify_command


def test_future_dated_valid_command_cannot_replay_after_default_guard_ttl():
    clock = [1000]
    guard = ReplayGuard(clock=lambda: clock[0])
    envelope = make_command('d', 'echo', {}, b'fixture', issued_at=1300)
    verify_command(envelope, b'fixture', replay_guard=guard, now=1000)
    clock[0] = 1301
    with pytest.raises(ValueError, match='replayed'):
        verify_command(envelope, b'fixture', replay_guard=guard, now=1301)


def test_short_guard_ttl_cannot_allow_replay_within_signature_window():
    clock = [1000]
    guard = ReplayGuard(ttl_seconds=1, clock=lambda: clock[0])
    envelope = make_command('d', 'echo', {}, b'fixture', issued_at=1000)
    verify_command(envelope, b'fixture', replay_guard=guard, now=1000)
    clock[0] = 1002
    with pytest.raises(ValueError, match='replayed'):
        verify_command(envelope, b'fixture', replay_guard=guard, now=1002)


def test_replay_blocked_at_inclusive_final_valid_second():
    clock = [1000]
    guard = ReplayGuard(clock=lambda: clock[0])
    envelope = make_command('d', 'echo', {}, b'fixture', issued_at=1300)
    verify_command(envelope, b'fixture', replay_guard=guard, now=1000)
    clock[0] = 1600
    with pytest.raises(ValueError, match='replayed'):
        verify_command(envelope, b'fixture', replay_guard=guard, now=1600)
    clock[0] = 1601
    with pytest.raises(ValueError, match='stale'):
        verify_command(envelope, b'fixture', replay_guard=guard, now=1601)

"""Explicit conversation IDs must not silently resolve to a different owner/channel."""
import pytest

from meemee.companion.store import CompanionStore


@pytest.mark.parametrize('owner,channel', [('other', 'local'), ('owner', 'webhook')])
def test_conversation_collision_fails_without_rebinding(tmp_path, owner, channel):
    store = CompanionStore(tmp_path / 'c.db')
    original = store.start_conversation('owner', 'local', 'explicit')
    with pytest.raises(ValueError, match='conversation.*already'):
        store.start_conversation(owner, channel, 'explicit')
    assert store.get_conversation('explicit') == original


def test_same_owner_channel_idempotent(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    assert store.start_conversation('owner', 'local', 'explicit') == store.start_conversation('owner', 'local', 'explicit')

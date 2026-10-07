"""A conversation publication must not leave orphan or half-published messages."""
import sqlite3

import pytest

from meemee.companion.store import CompanionStore


def test_failed_conversation_update_rolls_back_message(tmp_path):
    store = CompanionStore(tmp_path / 'companion.db')
    conv = store.start_conversation('owner', 'local')
    store.db.execute("""CREATE TRIGGER reject_message_timestamp BEFORE UPDATE ON companion_conversations
                      BEGIN SELECT RAISE(ABORT, 'timestamp blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='timestamp blocked'):
        store.add_message(conv['id'], 'assistant', 'must not survive')
    assert store.history(conv['id']) == []


def test_unknown_conversation_cannot_create_orphan_message(tmp_path):
    store = CompanionStore(tmp_path / 'companion.db')
    with pytest.raises(ValueError, match='unknown conversation'):
        store.add_message('missing', 'assistant', 'orphan')
    assert store.db.execute('SELECT count(*) FROM companion_messages').fetchone()[0] == 0

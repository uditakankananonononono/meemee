"""Interrupted companion deletion must roll back every content table."""
import sqlite3

import pytest

from meemee.companion.models import FactInput, UserProfile
from meemee.companion.store import CompanionStore


def test_mid_delete_failure_rolls_back_messages(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    store.upsert_user(UserProfile(user_id='o', display_name='Owner'))
    conversation = store.start_conversation('o', 'local')
    store.add_message(conversation['id'], 'user', 'fixture content')
    store.add_fact('o', FactInput(text='fixture fact'), source='test')
    store.db.execute("CREATE TRIGGER stop_fact_delete BEFORE DELETE ON companion_facts BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture failure'):
        store.delete_user_data('o')
    assert len(store.history(conversation['id'])) == 1
    assert store.get_conversation(conversation['id']) is not None

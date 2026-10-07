"""Model provenance must refer to the message in the named conversation."""

import pytest

from meemee.companion.store import CompanionStore


def test_trace_rejects_missing_message(tmp_path):
    store = CompanionStore(tmp_path / 'companion.sqlite3')
    conversation = store.start_conversation('owner', 'local')
    with pytest.raises(ValueError, match='message'):
        store.record_model_trace(900, conversation['id'], {'model': 'fabricated'})
    assert store.model_traces(conversation['id']) == []


def test_trace_rejects_wrong_conversation_without_replacing_valid_trace(tmp_path):
    store = CompanionStore(tmp_path / 'companion.sqlite3')
    own = store.start_conversation('owner', 'local')
    other = store.start_conversation('other', 'local')
    message = store.add_message(own['id'], 'assistant', 'reply')
    store.record_model_trace(message['id'], own['id'], {'model': 'original'})
    with pytest.raises(ValueError, match='conversation'):
        store.record_model_trace(message['id'], other['id'], {'model': 'fabricated'})
    assert store.model_traces(own['id'])[0]['model'] == 'original'
    assert store.model_traces(other['id']) == []

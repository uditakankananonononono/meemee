"""Queue state must not publish without its corresponding durable event."""
import sqlite3

import pytest

from meemee.jobs import JobStore


@pytest.mark.parametrize('action', ['enqueue', 'claim'])
def test_queue_event_write_fault_rolls_back_state(tmp_path, action):
    store = JobStore(tmp_path / 'j.db')
    ident = store.enqueue('fixture', principal='owner') if action == 'claim' else None
    store.db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON job_events BEGIN SELECT RAISE(ABORT,'fixture event unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match='event unavailable'):
        if action == 'enqueue':
            store.enqueue('fixture', principal='owner')
        else:
            store.claim()
    if action == 'enqueue':
        assert store.list_for_principal('owner')[0] == []
    else:
        assert store.get(ident)['status'] == 'queued'
        assert store.get(ident)['attempts'] == 0

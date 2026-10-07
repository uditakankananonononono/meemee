"""Cancelled state and its event must publish together."""
import sqlite3

import pytest

from meemee.jobs import JobStore


@pytest.mark.parametrize('action', ['cancel', 'cancel_running'])
def test_cancel_event_abort_rolls_back_state(tmp_path, action):
    store = JobStore(tmp_path / 'j.db')
    ident = store.enqueue('fixture', principal='owner')
    expected = 'queued'
    if action == 'cancel_running':
        store.claim()
        store.request_cancel(ident)
        expected = 'cancel_requested'
    store.db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON job_events BEGIN SELECT RAISE(ABORT,'fixture event unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match='event unavailable'):
        getattr(store, action)(ident)
    assert store.get(ident)['status'] == expected

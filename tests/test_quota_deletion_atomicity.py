"""Deleting quota data must not leave partial customer state on an SQL error."""

import sqlite3

import pytest

from meemee.quotas import QuotaStore


def test_quota_deletion_rolls_back_usage_when_limit_delete_fails(tmp_path):
    store = QuotaStore(tmp_path / 'quota.sqlite3')
    store.set_limit('owner', 5)
    store.consume_job('owner')
    store.set_limit('other', 7)
    store.consume_job('other')
    store.db.executescript("""
        CREATE TRIGGER refuse_limit_delete BEFORE DELETE ON quota_limits
        WHEN OLD.principal='owner'
        BEGIN SELECT RAISE(ABORT, 'injected quota deletion failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        store.delete_principal('owner')
    assert store.status('owner')['used'] == 1
    assert store.limit('owner') == 5
    assert store.status('other')['used'] == 1
    store.db.execute('DROP TRIGGER refuse_limit_delete')
    assert store.delete_principal('owner') == {'quota_usage': 1, 'quota_limits': 1}
    assert store.status('other')['used'] == 1
    assert store.limit('other') == 7

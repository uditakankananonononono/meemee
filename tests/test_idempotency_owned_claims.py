"""Opt-in opaque ownership fences protect replacement pending claims."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.idempotency import IdempotencyConflict, IdempotencyInProgress


def expire(store, backend):
    if backend == 'sqlite':
        with store.db:
            store.db.execute("UPDATE idempotency SET expires_at='2000-01-01T00:00:00+00:00'")
    else:
        with store.db.transaction() as c:
            c.execute("UPDATE meemee_idempotency SET expires_at='2000-01-01T00:00:00+00:00'")


@pytest.mark.parametrize('action', ['release', 'put'])
@pytest.mark.parametrize('old_token', ['old', None])
def test_replacement_owned_claim_rejects_stale_or_unfenced_writer(persistence, action, old_token):  # noqa: F811
    store = persistence.idempotency
    payload = {'goal': 'same'}
    store.claim('owner', '/jobs', 'key', payload, claim_token='old')
    expire(store, persistence.backend)
    assert store.claim('owner', '/jobs', 'key', payload, claim_token='new') is None
    if action == 'release':
        store.release('owner', '/jobs', 'key', claim_token=old_token)
    else:
        with pytest.raises(IdempotencyConflict):
            store.put('owner', '/jobs', 'key', payload, 200, {'id': 'stale'}, claim_token=old_token)
    with pytest.raises(IdempotencyInProgress):
        store.get('owner', '/jobs', 'key', payload)
    store.put('owner', '/jobs', 'key', payload, 200, {'id': 'new'}, claim_token='new')
    assert store.get('owner', '/jobs', 'key', payload) == (200, {'id': 'new'})


def test_expired_owned_claim_cannot_publish_without_replacement(persistence):  # noqa: F811
    store = persistence.idempotency
    store.claim('owner', '/jobs', 'key', {}, claim_token='old')
    expire(store, persistence.backend)
    with pytest.raises(IdempotencyConflict):
        store.put('owner', '/jobs', 'key', {}, 200, {}, claim_token='old')


def test_sqlite_upgrade_and_old_cutover_projection(tmp_path):
    import sqlite3

    from meemee.idempotency import IdempotencyStore
    from meemee_persist_pg.cutover import SPECS, source_columns

    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE idempotency(principal TEXT, route TEXT, key TEXT, request_hash TEXT, response TEXT, status INTEGER, created_at TEXT, expires_at TEXT, PRIMARY KEY(principal,route,key))')
        spec = SPECS['idempotency'][0]
        assert 'NULL AS claim_token' in source_columns(spec, db)
    store = IdempotencyStore(path)
    store.claim('owner', '/jobs', 'key', {}, claim_token='new')
    store.put('owner', '/jobs', 'key', {}, 200, {'id': 'new'}, claim_token='new')
    assert store.get('owner', '/jobs', 'key', {}) == (200, {'id': 'new'})


def test_http_job_passes_unique_owner_token_through_publication(monkeypatch):
    from types import SimpleNamespace

    from meemee import api

    seen = []
    monkeypatch.setattr(api.idempotency, 'claim', lambda *args, **kw: seen.append(('claim', kw['claim_token'])))
    monkeypatch.setattr(api.idempotency, 'put', lambda *args, **kw: seen.append(('put', kw['claim_token'])))
    monkeypatch.setattr(api.quotas, 'consume_job', lambda owner: {'used': 1})
    monkeypatch.setattr(api.jobs, 'enqueue', lambda *args, **kw: 'job')
    monkeypatch.setattr(api.audit, 'append', lambda *args: None)
    for _ in range(2):
        assert api.create_job(api.JobRequest(goal='work'), SimpleNamespace(id='owner'), 'key')['id'] == 'job'
    assert seen[0][1] == seen[1][1] and seen[2][1] == seen[3][1]
    assert seen[0][1] != seen[2][1] and len(seen[0][1]) >= 32

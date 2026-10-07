"""Failed imports must not restore old file copies over unrelated committed work."""
import hashlib
import json

import pytest

from meemee.account_export import export_account, import_account
from meemee.entitlements import EntitlementStore
from meemee.jobs import JobStore
from meemee.runs import RunStore


def test_import_failure_preserves_unrelated_committed_job(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    JobStore(source / 'jobs.sqlite3').db.close()
    RunStore(source / 'runs.sqlite3').db.close()
    EntitlementStore(source / 'entitlements.sqlite3').db.close()
    export = tmp_path / 'export.json'
    export_account(source, 'u', export)
    envelope = json.loads(export.read_text())
    envelope['payload']['jobs'] = [{'id': 'broken-import', 'unsupported_column': 1}]
    envelope['sha256'] = hashlib.sha256(json.dumps(envelope['payload'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    export.write_text(json.dumps(envelope))
    target = tmp_path / 'target'
    target.mkdir()
    stores = [JobStore(target / 'jobs.sqlite3'), RunStore(target / 'runs.sqlite3'), EntitlementStore(target / 'entitlements.sqlite3')]
    for store in stores:
        store.db.execute('PRAGMA journal_mode=DELETE')
        store.db.close()
    from meemee import account_export

    original = account_export.inspect_import
    injected = []

    def concurrent_commit(*args, **kwargs):
        plan = original(*args, **kwargs)
        jobstore = JobStore(target / 'jobs.sqlite3')
        injected.append(jobstore.enqueue('unrelated committed work', principal='other'))
        jobstore.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        jobstore.db.close()
        return plan

    monkeypatch.setattr(account_export, 'inspect_import', concurrent_commit)
    with pytest.raises(ValueError, match='unsupported'):
        import_account(target, export, 'new')
    assert JobStore(target / 'jobs.sqlite3').get(injected[0]) is not None
    assert JobStore(target / 'jobs.sqlite3').list_for_principal('new')[0] == []


def test_import_rolls_back_inserted_job_when_later_run_insert_fails(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    JobStore(source / 'jobs.sqlite3').enqueue('fixture imported', principal='u')
    RunStore(source / 'runs.sqlite3')
    EntitlementStore(source / 'entitlements.sqlite3')
    export = tmp_path / 'export.json'
    export_account(source, 'u', export)
    envelope = json.loads(export.read_text())
    envelope['payload']['runs'] = [{'run_id': 'broken', 'unsupported_column': 1}]
    envelope['sha256'] = hashlib.sha256(json.dumps(envelope['payload'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    export.write_text(json.dumps(envelope))
    target = tmp_path / 'target'
    target.mkdir()
    jobs = JobStore(target / 'jobs.sqlite3')
    existing = jobs.enqueue('unrelated', principal='other')
    RunStore(target / 'runs.sqlite3')
    EntitlementStore(target / 'entitlements.sqlite3')
    with pytest.raises(ValueError, match='unsupported'):
        import_account(target, export, 'new')
    assert jobs.list_for_principal('new')[0] == []
    assert jobs.get(existing)['goal'] == 'unrelated'

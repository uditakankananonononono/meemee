"""Local agency state must be exported and fully deleted with the account."""
import json
import sqlite3

from meemee.account_deletion import build_account_purger
from meemee.account_export import export_account
from meemee.agency import AgencyWorker
from meemee.config import Settings
from meemee.intake import IntakeService
from meemee.monitors import MonitorInput
from meemee.persistence import persistence_from_settings

FEED = '<rss><channel><item><guid>1</guid><title>Ready</title><description>{}</description></item></channel></rss>'
TABLES = {  # file -> (table, owner column)
    'goals.sqlite3': [('agency_goals', 'principal'), ('agency_notes', 'principal'),
                      ('agency_step_grants', 'principal'), ('agency_execution_events', 'principal')],
    'context.sqlite3': [('context_sources', 'owner_id'), ('context_records', 'owner_id')],
    'intake.sqlite3': [('intake_snapshots', 'owner')],
    'source-health.sqlite3': [('source_health', 'owner_id'), ('source_health_checks', 'owner_id')],
    'monitors.sqlite3': [('monitors', 'owner_id'), ('monitor_intake', 'owner_id'),
                         ('monitor_outbox', 'owner_id'), ('monitor_notifications', 'owner_id')],
}


def count(data, filename, table, column, who):
    db = sqlite3.connect(data / filename)
    try:
        return db.execute(f'SELECT count(*) FROM {table} WHERE {column}=?', (who,)).fetchone()[0]
    finally:
        db.close()


def seed(data, root, who, word):
    (root / f'{who}.xml').write_text(FEED.format(f'ready {word}'))
    service = IntakeService(data, root)
    service.context.register_source(who, 'feed', 'local_rss', {'path': f'{who}.xml', 'interval_seconds': 5})
    service.monitors.create(who, MonitorInput(name='Ready', source_id='feed', field='content', operator='contains', expected='ready'))
    goal = service.goals.create(who, 'Record it', context={'steps': [{'kind': 'note', 'text': f'note {word}'}]})
    assert service.tick(force=True)['failed'] == 0
    service.goals.approve_step(who, goal['id'], 0)
    assert AgencyWorker(service.goals, service.context, 'w').tick(who) == 'completed'
    return service


def test_export_includes_and_deletion_removes_all_local_agency_state(tmp_path, monkeypatch):
    data, root = tmp_path / 'data', tmp_path / 'feeds'
    root.mkdir()
    seed(data, root, 'alice', 'zebra')
    seed(data, root, 'bob', 'yak')
    for filename, tables in TABLES.items():
        for table, column in tables:
            assert count(data, filename, table, column, 'alice') >= 1, (filename, table)

    out = tmp_path / 'export.json'
    report = export_account(data, 'alice', out)
    text = out.read_text()
    payload = json.loads(text)['payload']
    agency = payload['local_agency']
    assert 'zebra' in text and 'yak' not in text and 'bob' not in text
    for key in ('goals', 'notes', 'step_grants', 'execution_events', 'context_sources', 'context_records',
                'intake_snapshots', 'source_health', 'source_health_checks', 'monitors',
                'monitor_intake', 'monitor_outbox', 'monitor_notifications'):
        assert len(agency[key]) >= 1, key
    assert report['local_agency'] == {k: len(v) for k, v in agency.items()}

    monkeypatch.setenv('MEEMEE_DATA_DIR', str(data))
    settings = Settings()
    result = build_account_purger(settings, persistence_from_settings(settings)).purge('alice', requested_by='test')
    assert result['status'] == 'completed'
    for filename, tables in TABLES.items():
        for table, column in tables:
            assert count(data, filename, table, column, 'alice') == 0, (filename, table)
            assert count(data, filename, table, column, 'bob') >= 1, ('bob kept', filename, table)
    # progress/dependency rows hang off goals; none may remain for alice's goals.
    db = sqlite3.connect(data / 'goals.sqlite3')
    assert db.execute('SELECT count(*) FROM agency_progress WHERE goal_id NOT IN (SELECT id FROM agency_goals)').fetchone()[0] == 0
    db.close()
    # Nothing of alice's content survives anywhere in the raw files (including WAL/FTS).
    for path in data.glob('*.sqlite3*'):
        assert b'zebra' not in path.read_bytes(), path.name

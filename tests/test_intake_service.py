from meemee.context import ContextStore
from meemee.intake import IntakeService
from meemee.monitors import MonitorInput, MonitorStore
from meemee.source_health import SourceHealthStore


def test_real_file_feed_restart_update_delete_and_monitor(tmp_path):
    root = tmp_path / 'feeds'; root.mkdir()
    feed = root / 'feed.xml'
    feed.write_text('<rss><channel><item><guid>1</guid><title>Ready</title><description>ready now</description></item></channel></rss>')
    data = tmp_path / 'data'
    context = ContextStore(data / 'context.sqlite3')
    context.register_source('o','feed','local_rss',{'path': 'feed.xml', 'interval_seconds': 5})
    monitors = MonitorStore(data / 'monitors.sqlite3')
    monitor = monitors.create('o', MonitorInput(name='Ready', source_id='feed',field='content',operator='contains',expected='ready'))
    service = IntakeService(data, root)
    assert service.tick(force=True) == {'ok': 1, 'failed': 0}
    assert context.recent('o')[0]['content'] == 'ready now'
    assert monitors.get('o',monitor['id'])['fire_count'] == 1
    assert len(monitors.notifications('o')) == 1
    assert IntakeService(data, root).tick(force=True)['ok'] == 1
    assert len(context.recent('o')) == 1
    feed.write_text('<rss><channel><item><guid>1</guid><title>Changed</title><description>updated</description></item></channel></rss>')
    service.tick(force=True)
    assert context.recent('o')[0]['content'] == 'updated'
    assert len(context.recent('o')) == 1
    feed.write_text('<rss><channel/></rss>')
    service.tick(force=True)
    assert context.recent('o') == []
    assert SourceHealthStore(data / 'source-health.sqlite3').status('o','feed')['status'] == 'healthy'


def test_revoke_malformed_oversize_escape_and_no_cross_owner(tmp_path):
    root = tmp_path / 'feeds'; root.mkdir()
    (root/'bad.xml').write_text('not xml')
    data = tmp_path / 'data'; service = IntakeService(data, root)
    context = ContextStore(data / 'context.sqlite3')
    context.register_source('o','bad','local_rss', {'path':'bad.xml'})
    assert service.tick(force=True)['failed'] == 1
    assert context.recent('o') == []
    context.register_source('o','escape','local_rss', {'path':'../outside.xml'})
    context.register_source('o','network','rss', {'url':'http://127.0.0.1/private'})
    context.register_source('o','off','local_rss', {'path':'bad.xml','enabled':False})
    assert service.tick(force=True) == {'ok':0,'failed':3}
    assert context.recent('other') == []
    assert SourceHealthStore(data / 'source-health.sqlite3').get('o','off') is None
    (root/'bad.xml').write_text('x'*2_000_001)
    service.tick(force=True)
    assert SourceHealthStore(data / 'source-health.sqlite3').get('o','bad')['last_error'] == 'ValueError'


def test_interrupted_cross_database_intent_recovers(tmp_path):
    import sqlite3

    import pytest
    root = tmp_path/'feeds'; root.mkdir()
    (root/'rss.xml').write_text('<rss><channel><item><guid>1</guid><title>Ready</title><description>ready</description></item></channel></rss>')
    data = tmp_path/'data'; service = IntakeService(data,root)
    service.context.register_source('o','s','local_rss',{'path':'rss.xml'})
    service.context.db.execute("CREATE TRIGGER reject_insert BEFORE INSERT ON context_records BEGIN SELECT RAISE(ABORT, 'fault'); END")
    with pytest.raises(sqlite3.IntegrityError): service.tick(force=True)
    assert service.db.execute('SELECT pending FROM intake_snapshots').fetchone()[0] == 1
    service.context.db.execute('DROP TRIGGER reject_insert')
    restarted = IntakeService(data,root)
    assert restarted.tick() == {'ok':1,'failed':0}
    assert len(restarted.context.recent('o')) == 1
    assert restarted.db.execute('SELECT pending FROM intake_snapshots').fetchone()[0] == 0


def test_malformed_calendar_never_deletes_last_good_snapshot(tmp_path):
    root = tmp_path/'feeds'; root.mkdir()
    feed = root/'events.ics'
    feed.write_text('BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nSUMMARY:Meeting\nDTSTART:20990101T000000Z\nEND:VEVENT\nEND:VCALENDAR\n')
    data = tmp_path/'data'; service = IntakeService(data,root)
    service.context.register_source('o','calendar','local_ics',{'path':'events.ics'})
    assert service.tick(force=True) == {'ok':1,'failed':0}
    feed.write_text('broken')
    assert service.tick(force=True) == {'ok':0,'failed':1}
    assert len(service.context.recent('o')) == 1

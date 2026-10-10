from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import json
import pytest
from meemee.monitors import MonitorStore,MonitorInput,MonitorEventConflict

@pytest.fixture(params=['sqlite','pg'])
def make(request,tmp_path):
    handles=[]
    if request.param=='sqlite':
        def factory():
            s=MonitorStore(tmp_path/'m.db');handles.append(s.db);return s
        yield factory
    else:
        import pgserver
        from meemee_persist_pg import Database,MigrationStore
        from meemee_persist_pg.monitors import MonitorStore as PG
        server=pgserver.get_server(tmp_path/'pg',cleanup_mode='stop')
        db=Database(server.get_uri(),max_size=12);applied=MigrationStore(db).apply()
        with db.transaction() as c: print('ACTUAL_POSTGRES_VERSION',c.execute('SELECT version() AS v').fetchone()['v'])
        assert 19 in applied;print('ACTUAL_MIGRATION019_APPLIED')
        yield lambda:PG(db)
        db.close();server.cleanup()
    for h in handles:h.close()

def mon(s,owner='a',source='s'):
    return s.create(owner,MonitorInput(name='m',source_id=source,field='v',operator='eq',expected='yes',max_fires=20))

def ev(s,owner='a',source='s',ident='i',payload=None):
    return s.evaluate_identified(owner,source,{'v':'yes'} if payload is None else payload,event_id=ident)

def test_concurrent_reopen_replay_conflict_and_identity_scopes(make):
    s=make();m=mon(s);handles=[make() for _ in range(8)]
    with ThreadPoolExecutor(8) as pool:out=list(pool.map(ev,handles))
    assert sum(x['status']=='new' for x in out)==1
    assert sum(len(x['fired']) for x in out)==1
    fresh=make();assert ev(fresh)['status']=='replay'
    with pytest.raises(MonitorEventConflict):ev(fresh,payload={'v':'other'})
    assert fresh.get('a',m['id'])['fire_count']==1
    assert ev(fresh,ident='distinct')['fired']==[m['id']]
    other=mon(fresh,'b');assert ev(fresh,owner='b')['fired']==[other['id']]
    second=mon(fresh,source='t');assert ev(fresh,source='t')['fired']==[second['id']]
    assert fresh.evaluate('a','s',{'v':'yes'})==[m['id']]
    assert fresh.evaluate('a','s',{'v':'yes'})==[m['id']]

def test_reservation_fire_atomic_rollback(make,monkeypatch):
    s=make();m=mon(s)
    original=s._event
    def fail(*args,**kw):raise RuntimeError('injected event append failure')
    monkeypatch.setattr(s,'_event',fail)
    with pytest.raises(RuntimeError):ev(s)
    monkeypatch.setattr(s,'_event',original)
    assert s.get('a',m['id'])['fire_count']==0
    assert ev(make())['status']=='new'
    assert s.get('a',m['id'])['fire_count']==1

def test_delete_owner_purges_identity(make):
    s=make();mon(s);ev(s);s.delete_owner('a')
    assert ev(s)['status']=='new'

def test_http_scope_fixed_errors_and_legacy(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from meemee import api
    s=MonitorStore(tmp_path/'api.db');monkeypatch.setattr(api,'monitors',s)
    c=TestClient(api.app);h={'Authorization':'Bearer test-bootstrap-token'}
    m=mon(s,'bootstrap');body={'source_id':'s','event_id':'i','event':{'v':'yes'}}
    assert c.post('/v1/monitors/evaluate',json=body).status_code==401
    _,tok=api.tokens.create('read',{'jobs:read'})
    assert c.post('/v1/monitors/evaluate',headers={'Authorization':f'Bearer {tok}'},json=body).status_code==403
    assert c.post('/v1/monitors/evaluate',headers=h,json=body).json()['status']=='new'
    assert c.post('/v1/monitors/evaluate',headers=h,json=body).json()['fired']==[]
    body['event']={'v':'no'}
    r=c.post('/v1/monitors/evaluate',headers=h,json=body);assert r.status_code==409 and r.json()['detail']=='monitor event identity conflict'
    body['event_id']='x\n'
    assert c.post('/v1/monitors/evaluate',headers=h,json=body).status_code==422
    del body['event_id'];body['event']={'v':'yes'}
    assert c.post('/v1/monitors/evaluate',headers=h,json=body).json()['status']=='legacy'
    assert s.get('bootstrap',m['id'])['fire_count']==2
    s.db.close()

def init_portable(path):
    from meemee.jobs import JobStore
    from meemee.runs import RunStore
    from meemee.entitlements import EntitlementStore
    path.mkdir(exist_ok=True)
    JobStore(path/'jobs.sqlite3');RunStore(path/'runs.sqlite3');EntitlementStore(path/'entitlements.sqlite3')
    return MonitorStore(path/'monitors.sqlite3')


def test_portable_identity_transfer_all_backend_directions_and_conflict(tmp_path):
    import pgserver
    from meemee.account_export import export_account,import_account
    from meemee_persist_pg import Database,MigrationStore
    from meemee_persist_pg.monitors import MonitorStore as PG
    from meemee_persist_pg.operator_tools import export_account as pg_export,import_account as pg_import
    src=tmp_path/'src';s=init_portable(src);mon(s);ev(s)
    export=tmp_path/'src.json';export_account(src,'a',export)
    assert len(json.loads(export.read_text())['payload']['monitor_source_events'])==1
    dst=tmp_path/'dst';d=init_portable(dst);import_account(dst,export,'b')
    assert ev(d,owner='b')['status']=='replay'
    with pytest.raises(ValueError,match='collision'):import_account(dst,export,'b')
    server=pgserver.get_server(tmp_path/'pgtransfer',cleanup_mode='stop');db=Database(server.get_uri())
    MigrationStore(db).apply();p=PG(db)
    try:
        pg_import(db,export,'c');assert ev(p,owner='c')['status']=='replay'
        pgfile=tmp_path/'pg.json';pg_export(db,'c',pgfile)
        assert len(json.loads(pgfile.read_text())['payload']['monitor_source_events'])==1
        pg_import(db,pgfile,'e');assert ev(p,owner='e')['status']=='replay'
        final=tmp_path/'final';f=init_portable(final);import_account(final,pgfile,'d');assert ev(f,owner='d')['status']=='replay'
        f.db.close()
    finally:db.close();server.cleanup();s.db.close();d.db.close()


def test_identity_transfer_collision_rolls_back_other_rows(tmp_path):
    from meemee.account_export import export_account,import_account
    from meemee.jobs import JobStore
    src=tmp_path/'src';s=init_portable(src);ev(s)
    j=JobStore(src/'jobs.sqlite3');j.enqueue('must not import',principal='a')
    export=tmp_path/'src.json';export_account(src,'a',export)
    dst=tmp_path/'dst';d=init_portable(dst);ev(d,owner='b')
    with pytest.raises(ValueError,match='monitor identity collision'):import_account(dst,export,'b')
    assert JobStore(dst/'jobs.sqlite3').list_for_principal('b')[0]==[]
    s.db.close();d.db.close()


def test_cutover_preserves_identity_and_replay(tmp_path):
    import pgserver
    from meemee_persist_pg import Database,MigrationStore
    from meemee_persist_pg.monitors import MonitorStore as PG
    from meemee_persist_pg.cutover import Cutover
    from meemee.memory import MemoryStore
    from meemee.plan_store import PlanStore
    from meemee.jobs import JobStore
    from meemee.audit import AuditLog
    from meemee.auth import TokenStore
    src=tmp_path/'cut';src.mkdir()
    for cls,name in [(MemoryStore,'meemee'),(PlanStore,'plans'),(JobStore,'jobs'),(AuditLog,'audit'),(TokenStore,'auth')]:cls(src/f'{name}.sqlite3')
    s=MonitorStore(src/'monitors.sqlite3');m=mon(s);ev(s)
    server=pgserver.get_server(tmp_path/'pgcut',cleanup_mode='stop');db=Database(server.get_uri());MigrationStore(db).apply()
    try:
        sources={g:src/f'{name}.sqlite3' for g,name in [('memory','meemee'),('plans','plans'),('jobs','jobs'),('audit','audit'),('tokens','auth'),('monitors','monitors')]}
        c=Cutover(db,sources);assert c.copy()['meemee_monitor_source_events']==1
        assert all(v['match'] for v in c.verify().values())
        p=PG(db);assert ev(p)['status']=='replay';assert p.get('a',m['id'])['fire_count']==1
    finally:db.close();server.cleanup();s.db.close()


def test_api_same_identity_different_authenticated_owners(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from meemee import api
    s=MonitorStore(tmp_path/'owners.db');monkeypatch.setattr(api,'monitors',s)
    a=mon(s,'alice');b=mon(s,'bob');c=TestClient(api.app)
    body={'source_id':'s','event_id':'same','event':{'v':'yes'},'owner_id':'alice'}
    for owner in ['alice','bob']:
        _,tok=api.tokens.create(owner,{'runs:write'},owner_id=owner)
        r=c.post('/v1/monitors/evaluate',headers={'Authorization':f'Bearer {tok}'},json=body)
        assert r.status_code==200 and r.json()['status']=='new'
    assert s.get('alice',a['id'])['fire_count']==s.get('bob',b['id'])['fire_count']==1
    s.db.close()


def test_legacy_cutover_identity_table_absence_is_empty(tmp_path):
    import sqlite3
    from meemee_persist_pg.cutover import source_query,SPECS
    db=sqlite3.connect(tmp_path/'old.db')
    spec=next(s for s in SPECS['monitors'] if s.source=='monitor_source_events')
    assert db.execute(source_query(spec,db)).fetchall()==[]
    db.close()


def test_identity_survives_fresh_python_process(tmp_path):
    import os,subprocess,sys
    path=tmp_path/'restart.db';s=MonitorStore(path);m=mon(s);ev(s);s.db.close()
    code="from pathlib import Path;from meemee.monitors import MonitorStore;s=MonitorStore(Path(__import__('sys').argv[1]));r=s.evaluate_identified('a','s',{'v':'yes'},event_id='i');assert r=={'fired':[],'status':'replay'};assert s.get('a',__import__('sys').argv[2])['fire_count']==1;s.db.close();print('FRESH_PROCESS_REPLAY_NOOP')"
    r=subprocess.run([sys.executable,'-c',code,str(path),m['id']],capture_output=True,text=True,timeout=15)
    assert r.returncode==0,r.stderr
    assert 'FRESH_PROCESS_REPLAY_NOOP' in r.stdout


def test_invalid_import_identity_refuses_before_state_change(tmp_path):
    from meemee.account_export import validated_identity_rows
    from meemee.monitor_event_identity import identify_event
    good={'owner_id':'a','source_id':'s','event_id':'i','payload_sha256':identify_event('a','s','i',{}).identity.payload_sha256,'created_at':'2026-10-10T00:00:00+00:00'}
    for bad in [dict(good,owner_id='b'),dict(good,payload_sha256='X'*64),dict(good,created_at='2026-10-10'),dict(good,event_id='i\n')]:
        with pytest.raises((ValueError,TypeError)):
            validated_identity_rows({'principal':'a','monitor_source_events':[bad]})
    with pytest.raises(ValueError):validated_identity_rows({'principal':'a','monitor_source_events':[good,good]})

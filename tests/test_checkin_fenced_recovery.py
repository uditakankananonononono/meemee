import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from meemee.companion.checkin_leases import LostCheckinClaim
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


@pytest.fixture(params=['sqlite','pg'])
def make(request,tmp_path):
    handles=[]
    if request.param=='sqlite':
        def factory():
            s=CompanionStore(tmp_path/'companion.db');handles.append(s.db);return s
        yield factory
    else:
        import pgserver

        from meemee_persist_pg import Database, MigrationStore
        from meemee_persist_pg.companion import CompanionStore as PG
        server=pgserver.get_server(tmp_path/'pg',cleanup_mode='stop')
        db=Database(server.get_uri(),max_size=12);assert 20 in MigrationStore(db).apply()
        with db.transaction() as c:print('ACTUAL_PG',c.execute('SELECT version() AS v').fetchone()['v'],'MIGRATION020')
        try:yield lambda:PG(db)
        finally:db.close();server.cleanup()
    for h in handles:h.close()


def queue(s,max_attempts=3):
    now=datetime.now(timezone.utc)
    s.upsert_user(UserProfile(user_id='u',display_name='U',checkins=CheckInPreferences(enabled=True)))
    row,_=s.schedule_checkin('u',now-timedelta(seconds=1),'slot','local',None,max_attempts)
    return now,row


def test_independent_handles_one_claim_recovery_and_stale_fences(make):
    s=make();now,row=queue(s)
    handles=[make() for _ in range(8)]
    with ThreadPoolExecutor(8) as pool:out=list(pool.map(lambda h:h.claim_checkin_fenced(now,lease_seconds=3),handles))
    claims=[r for r in out if r];assert len(claims)==1
    a=claims[0];assert a['attempts']==1 and a['claim_generation']==1 and a['status']=='running'
    assert make().recover_checkin_claims(now+timedelta(seconds=2))['requeued']==0
    assert make().recover_checkin_claims(now+timedelta(seconds=3))['requeued']==1
    b=make().claim_checkin_fenced(now+timedelta(seconds=3),lease_seconds=3)
    assert b['attempts']==2 and b['claim_generation']==2 and b['claim_token']!=a['claim_token']
    for action in [lambda:s.finish_checkin_claim(a,'wrong',now+timedelta(seconds=4)),lambda:s.fail_checkin_claim(a,'wrong',now+timedelta(seconds=4)),lambda:s.start_checkin_delivery(a,now+timedelta(seconds=4)),lambda:s.cancel_checkin_claim(a,now+timedelta(seconds=4)),lambda:s.renew_checkin_claim(a,now+timedelta(seconds=4))]:
        with pytest.raises(LostCheckinClaim):action()
    s.finish_checkin(row['id'],'legacy cannot finish');s.cancel_claimed_checkin(row['id'])
    assert s.list_checkins('u')[0]['status']=='running'
    assert s.start_checkin_delivery(b,now+timedelta(seconds=4))
    with pytest.raises(LostCheckinClaim):s.start_checkin_delivery(b,now+timedelta(seconds=4))
    assert make().recover_checkin_claims(now+timedelta(seconds=6))['quarantined']==1
    assert s.list_checkins('u')[0]['delivery_state']=='unknown'
    assert make().claim_checkin_fenced(now+timedelta(seconds=7)) is None


def test_budget_permission_and_claim_clock(make):
    s=make();now,_=queue(s,1);a=s.claim_checkin_fenced(now,lease_seconds=3)
    for bad in [dict(a,user_id='other'),dict(a,claim_generation=True),dict(a,claim_token='different')]:
        with pytest.raises(LostCheckinClaim):s.start_checkin_delivery(bad,now)
    with pytest.raises(LostCheckinClaim):s.start_checkin_delivery(a,now-timedelta(seconds=1))
    assert s.recover_checkin_claims(now+timedelta(seconds=3))['exhausted']==1
    assert s.claim_checkin_fenced(now+timedelta(seconds=4)) is None


def test_local_commit_atomic_and_preference_revocation(make):
    s=make();now,_=queue(s);a=s.claim_checkin_fenced(now)
    conv=s.start_conversation('u','local')
    assert s.finish_local_checkin(a['id'],conv['id'],'once',claim=a)
    with pytest.raises(LostCheckinClaim):s.finish_local_checkin(a['id'],conv['id'],'twice',claim=a)
    assert len(s.history(conv['id']))==1 and s.list_checkins('u')[0]['status']=='done'


def test_intent_rechecks_permission_under_transaction(make):
    s=make();now,_=queue(s);a=s.claim_checkin_fenced(now)
    p=s.profile('u');p.checkins.enabled=False;s.upsert_user(p)
    assert s.start_checkin_delivery(a,now) is False
    assert s.list_checkins('u')[0]['status']=='cancelled'


def test_legacy_running_unknown_is_not_retried(make):
    s=make();now,_=queue(s);assert s.claim_checkin(now)
    assert make().recover_checkin_claims(now)['quarantined']==1
    assert s.list_checkins('u')[0]['status']=='failed'
    assert s.claim_checkin_fenced(now) is None


@pytest.mark.asyncio
async def test_real_worker_recovers_unstarted_and_renews_during_generation(tmp_path):
    from test_companion_worker import queue_checkin, setup

    from meemee.companion.worker import deliver_due_once
    s,engine,channels=setup(tmp_path);queue_checkin(s)
    old=s.claim_checkin_fenced(datetime.now(timezone.utc)-timedelta(seconds=10),lease_seconds=3)
    # Queue was due60s earlier; simulate expired generation before model work.
    assert old
    entered=asyncio.Event()
    async def slow(user):entered.set();await asyncio.sleep(1.2);return 'renewed message'
    engine.checkin_message=slow
    result=await deliver_due_once(s,engine,channels,lease_seconds=3)
    assert result['delivered']
    row=s.list_checkins('udita')[0];assert row['attempts']==2 and row['claim_generation']==2
    assert row['delivery_state']=='accepted'
    s.db.close()

@pytest.mark.parametrize('started',[False,True])
def test_actual_client_sigkill_recovers_only_unstarted(make,started):
    import os
    import signal
    import subprocess
    import sys
    s=make();_now,_=queue(s)
    pg=s._checkin_pg
    if pg:
        # Source-of-truth connection string for this ephemeral test only.
        with s.db.transaction() as c:
            socket=c.execute('SHOW unix_socket_directories').fetchone()['unix_socket_directories']
            port=c.execute('SHOW port').fetchone()['port']
        destination=f'postgresql://postgres@/postgres?host={socket}&port={port}'
    else:destination=str(s.db.execute('PRAGMA database_list').fetchone()['file'])
    code='''import sys,time,json
from pathlib import Path
from datetime import datetime,timezone
if sys.argv[1]=='pg':
 from meemee_persist_pg import Database
 from meemee_persist_pg.companion import CompanionStore
 s=CompanionStore(Database(sys.argv[2]))
else:
 from meemee.companion.store import CompanionStore
 s=CompanionStore(Path(sys.argv[2]))
a=s.claim_checkin_fenced(lease_seconds=3)
if sys.argv[3]=='yes':assert s.start_checkin_delivery(a)
print(json.dumps({'claimed_at':str(a['claimed_at'])}),flush=True)
time.sleep(60)
'''
    p=subprocess.Popen([sys.executable,'-c',code,'pg' if pg else 'sqlite',destination,'yes' if started else 'no'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=p.stdout.readline();assert line,line
        stamp=__import__('json').loads(line)['claimed_at'];clock=datetime.fromisoformat(stamp)+timedelta(seconds=4)
        os.kill(p.pid,signal.SIGKILL);assert p.wait(timeout=5)==-signal.SIGKILL
        result=make().recover_checkin_claims(clock)
        assert result['quarantined' if started else 'requeued']==1
        fresh=make().claim_checkin_fenced(clock)
        assert (fresh is None) is started
    finally:
        if p.poll() is None:p.kill();p.wait()


def test_generation_and_intent_survive_real_cutover(make,tmp_path):
    # Both fixtures exercise legacy column defaults; PG fixture additionally verifies
    # live SQLite->PG row copy with all lease/intent columns, no active deployment.
    import sqlite3

    from meemee_persist_pg.cutover import SPECS, insert_values, source_query
    s=make();now,_=queue(s)
    source=CompanionStore(tmp_path/'transfer.db');queue(source)
    a=source.claim_checkin_fenced(now);assert source.start_checkin_delivery(a,now)
    spec=next(x for x in SPECS['companion'] if x.source=='companion_checkins')
    row=source.db.execute(source_query(spec,source.db)).fetchone()
    assert row['claim_token']==a['claim_token'] and row['delivery_state']=='started'
    if s._checkin_pg:
        with s.db.transaction() as c:
            c.execute('DELETE FROM meemee_companion_checkins')
            c.execute('INSERT INTO '+spec.target+'('+','.join(spec.columns)+') VALUES('+','.join('%s' for _ in spec.columns)+')',insert_values(spec,row))
        with s.db.transaction() as c:
            assert c.execute('SELECT claim_token FROM meemee_companion_checkins').fetchone()['claim_token']==a['claim_token']
        copied=s.list_checkins('u')[0];assert copied['claim_generation']==a['claim_generation'] and copied['delivery_state']=='started'
        assert s.recover_checkin_claims(now+timedelta(seconds=301))['quarantined']==1
    old=sqlite3.connect(':memory:')
    old.execute('CREATE TABLE companion_checkins('+','.join(col+' TEXT' for col in spec.columns if col not in ['claim_token','claim_generation','claimed_at','lease_until','delivery_state'])+')')
    query=source_query(spec,old)
    assert 'NULL AS claim_token' in query and "'unknown' AS delivery_state" in query
    old.close();source.db.close()

@pytest.mark.asyncio
async def test_worker_lost_generation_cannot_publish_or_send(tmp_path):
    from test_companion_worker import queue_checkin, setup

    from meemee.companion.worker import deliver_due_once
    s,engine,channels=setup(tmp_path);queue_checkin(s)
    entered,release=asyncio.Event(),asyncio.Event()
    async def paused(user):entered.set();await release.wait();return 'stale'
    engine.checkin_message=paused
    task=asyncio.create_task(deliver_due_once(s,engine,channels))
    await entered.wait()
    # Adversarial state change representing an independently recovered newer claim.
    s.db.execute("UPDATE companion_checkins SET claim_generation=claim_generation+1,claim_token='new'")
    release.set();out=await task
    assert out['status']=='claim_lost'
    conv=s.latest_conversation('udita','local');assert conv is None or s.history(conv['id'])==[]
    assert s.list_checkins('udita')[0]['status']=='running'
    s.db.close()


@pytest.mark.asyncio
async def test_send_intent_commit_precedes_adapter_and_ambiguity_blocks_retry(tmp_path):
    from test_companion_worker import queue_checkin, setup

    from meemee.companion.worker import deliver_due_once
    s,engine,channels=setup(tmp_path);queue_checkin(s)
    calls=[]
    class Ambiguous:
        async def send(self,address,message):
            calls.append(message)
            other=CompanionStore(tmp_path/'c.db')
            assert other.list_checkins('udita')[0]['delivery_state']=='started'
            other.db.close()
            raise OSError('provider may have accepted')
    channels['local']=Ambiguous()
    out=await deliver_due_once(s,engine,channels)
    assert out['status']=='failed' and s.list_checkins('udita')[0]['delivery_state']=='unknown'
    assert await deliver_due_once(s,engine,channels)=={'claimed':False}
    assert len(calls)==1;s.db.close()


@pytest.mark.asyncio
async def test_renewal_storage_failure_stops_generation_before_send(tmp_path,monkeypatch):
    from test_companion_worker import queue_checkin, setup

    from meemee.companion.worker import deliver_due_once
    s,engine,channels=setup(tmp_path);queue_checkin(s)
    async def slow(user):await asyncio.sleep(30);return 'must not send'
    engine.checkin_message=slow
    def failure(*args,**kwargs):raise OSError('renewal storage unavailable')
    monkeypatch.setattr(s,'renew_checkin_claim',failure)
    with pytest.raises(OSError,match='renewal storage'):await deliver_due_once(s,engine,channels,lease_seconds=3)
    assert s.latest_conversation('udita','local') is None
    assert s.list_checkins('udita')[0]['status']=='queued'
    s.db.close()


def test_sqlite_atomic_schema_upgrade_legacy_running_refusal(tmp_path):
    import sqlite3
    path=tmp_path/'legacy.db';s=CompanionStore(path);now,_row=queue(s);s.claim_checkin(now);s.db.close()
    db=sqlite3.connect(path)
    for col in ['claim_token','claim_generation','claimed_at','lease_until','delivery_state']:db.execute('ALTER TABLE companion_checkins DROP COLUMN '+col)
    db.commit();db.close()
    upgraded=CompanionStore(path)
    assert upgraded.recover_checkin_claims(now)['quarantined']==1
    assert upgraded.list_checkins('u')[0]['delivery_state']=='unknown'
    upgraded.db.close()


def test_exhausted_queued_legacy_never_gets_extra_attempt(make):
    s=make();now,row=queue(s,1)
    if s._checkin_pg:
        with s.db.transaction() as c:c.execute("UPDATE meemee_companion_checkins SET attempts=1 WHERE id=%s",(row['id'],))
    else:s.db.execute('UPDATE companion_checkins SET attempts=1 WHERE id=?',(row['id'],))
    assert s.claim_checkin_fenced(now) is None
    assert s.list_checkins('u')[0]['status']=='failed'


def test_claim_token_not_exposed_in_user_views(make):
    s=make();now,_=queue(s);a=s.claim_checkin_fenced(now)
    assert a['claim_token']
    assert 'claim_token' not in s.list_checkins('u')[0]
    assert 'claim_token' not in s.export_user_data('u')['checkins'][0]


def test_renewal_prevents_recovery_and_plan_does_not_expose_token(make):
    s=make();now,_row=queue(s);a=s.claim_checkin_fenced(now,lease_seconds=3)
    assert s.renew_checkin_claim(a,now+timedelta(seconds=2),lease_seconds=3)
    assert s.recover_checkin_claims(now+timedelta(seconds=3))['requeued']==0
    assert s.recover_checkin_claims(now+timedelta(seconds=5))['requeued']==1
    planned,created=s.schedule_checkin('u',now,'other','local',None,reuse_pending=True)
    assert not created and 'claim_token' not in planned
    same,created=s.schedule_checkin('u',now,'slot','local',None)
    assert not created and 'claim_token' not in same


@pytest.mark.asyncio
async def test_two_real_workers_one_publication(tmp_path):
    from test_companion_worker import queue_checkin, setup

    from meemee.companion.channels import LocalChannel
    from meemee.companion.worker import deliver_due_once
    s,engine,channels=setup(tmp_path);queue_checkin(s)
    other=CompanionStore(tmp_path/'c.db')
    results=await asyncio.gather(deliver_due_once(s,engine,channels),deliver_due_once(other,engine,{'local':LocalChannel(other)}))
    assert sum(bool(r.get('delivered')) for r in results)==1
    conv=s.latest_conversation('udita','local');assert len(s.history(conv['id']))==1
    other.db.close();s.db.close()


@pytest.mark.parametrize('action',['intent','renew','finish','fail','cancel','unknown','local'])
def test_blocked_row_lock_expiry_cannot_authorize_any_effect(make,action):
    import threading
    import time

    s=make();_now,_=queue(s);a=s.claim_checkin_fenced(lease_seconds=3)
    if action=='finish':assert s.start_checkin_delivery(a)
    conv=s.start_conversation('u','local') if action=='local' else None
    competitor=make();entered=threading.Event()
    # A real lock delays the claimant past its real3s lease; not a frozen clock.
    def call():
        entered.set()
        if action=='intent':return competitor.start_checkin_delivery(a)
        if action=='renew':return competitor.renew_checkin_claim(a,lease_seconds=3)
        if action=='finish':return competitor.finish_checkin_claim(a,'late')
        if action=='fail':return competitor.fail_checkin_claim(a,'late')
        if action=='cancel':return competitor.cancel_checkin_claim(a)
        if action=='unknown':return competitor.unknown_checkin_claim(a,'late')
        return competitor.finish_local_checkin(a['id'],conv['id'],'late',claim=a)
    with ThreadPoolExecutor(1) as pool:
        with s._lease_transaction() as lock:
            lock.execute('SELECT * FROM companion_checkins WHERE id=?'+lock.for_update,(a['id'],)).fetchone()
            future=pool.submit(call);assert entered.wait(1)
            time.sleep(3.3)
        with pytest.raises(LostCheckinClaim):future.result(timeout=5)
    row=s.list_checkins('u')[0]
    assert row['status']=='running' and row['delivery_state']==('started' if action=='finish' else 'not_started')
    if conv:assert s.history(conv['id'])==[]
    # Ensure an expired renewal did not resurrect this generation.
    assert s.recover_checkin_claims()['quarantined' if action=='finish' else 'requeued']==1


def test_acquisition_timestamps_are_fresh_after_transaction_wait(make):
    import threading
    import time

    s=make();queue(s);other=make();entered=threading.Event()
    def acquire():entered.set();return other.claim_checkin_fenced(lease_seconds=3)
    with ThreadPoolExecutor(1) as pool:
        with s._lease_transaction() as lock:
            # Force both PG acquisition UPDATE and SQLite BEGIN to wait.
            if s._checkin_pg:
                lock.execute('LOCK TABLE companion_checkins IN ACCESS EXCLUSIVE MODE')
            else:
                lock.execute('SELECT * FROM companion_checkins').fetchall()
            future=pool.submit(acquire);assert entered.wait(1);time.sleep(1)
        a=future.result(timeout=5)
    assert a is not None
    claimed=datetime.fromisoformat(str(a['claimed_at']))
    assert (datetime.now(timezone.utc)-claimed).total_seconds()<0.8

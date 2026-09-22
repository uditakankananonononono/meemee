from pathlib import Path

from meemee.monitors import MonitorInput, MonitorStore


def test_monitor_triggers_and_completes(tmp_path:Path):
 s=MonitorStore(tmp_path/'m.db');m=s.create('u',MonitorInput(name='invoice paid',source_id='mail',field='subject',operator='contains',expected='paid'))
 assert s.evaluate('u','mail',{'subject':'Invoice PAID'})==[m['id']]
 assert s.get('u',m['id'])['status']=='completed' and s.events('u',m['id'])[-1]['kind']=='triggered'
def test_monitor_owner_isolation_timeout_and_cancel(tmp_path:Path):
 s=MonitorStore(tmp_path/'m.db');m=s.create('u',MonitorInput(name='reply',source_id='mail',field='id',operator='exists',deadline='2026-01-01T00:00:00+00:00'))
 assert s.evaluate('other','mail',{'id':'x'})==[]
 assert s.evaluate('u','mail',{'id':'x'},at='2026-02-01T00:00:00+00:00')==[] and s.get('u',m['id'])['status']=='timed_out'
 active=s.create('u',MonitorInput(name='later',source_id='mail',field='id',operator='exists'))
 assert not s.cancel('other',active['id']) and s.cancel('u',active['id'])
def test_repeating_monitor_respects_fire_budget(tmp_path:Path):
 s=MonitorStore(tmp_path/'m.db');m=s.create('u',MonitorInput(name='threshold',source_id='sensor',field='value',operator='gte',expected=10,max_fires=2))
 assert s.evaluate('u','sensor',{'value':10})==[m['id']] and s.get('u',m['id'])['status']=='active'
 assert s.evaluate('u','sensor',{'value':11})==[m['id']] and s.get('u',m['id'])['status']=='completed'

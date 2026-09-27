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


def test_monitor_rejects_zero_fire_budget_and_ambiguous_deadline():
 from pydantic import ValidationError
 import pytest
 with pytest.raises(ValidationError):MonitorInput(name='bad',source_id='s',field='x',operator='exists',max_fires=0)
 with pytest.raises(ValidationError):MonitorInput(name='bad',source_id='s',field='x',operator='exists',deadline='2026-09-27T12:00:00')


def test_monitor_normalizes_deadline_and_evaluation_to_utc(tmp_path:Path):
 import pytest
 s=MonitorStore(tmp_path/'m.db')
 m=s.create('u',MonitorInput(name='window',source_id='s',field='ready',operator='eq',expected=True,deadline='2026-09-27T13:00:00+05:30'))
 assert m['deadline']=='2026-09-27T07:30:00+00:00'
 # The offset string sorts later as text but represents an earlier instant.
 assert s.evaluate('u','s',{'ready':True},at='2026-09-27T08:00:00+01:00')==[m['id']]
 m2=s.create('u',MonitorInput(name='expired',source_id='s',field='ready',operator='eq',expected=True,deadline='2026-09-27T13:00:00+05:30'))
 assert s.evaluate('u','s',{'ready':True},at='2026-09-27T13:31:00+05:30')==[]
 assert s.get('u',m2['id'])['status']=='timed_out'
 with pytest.raises(ValueError):s.evaluate('u','s',{'ready':True},at='2026-09-27T08:00:00')

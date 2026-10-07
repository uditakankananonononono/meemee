"""A monitor needs an authenticated product path for source events."""
from fastapi.testclient import TestClient

from meemee import api
from meemee.monitors import MonitorStore


def test_owner_source_event_can_trigger_monitor_through_api(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'monitors', MonitorStore(tmp_path / 'm.db'))
    client = TestClient(api.app)
    headers = {'Authorization': 'Bearer test-bootstrap-token'}
    created = client.post('/v1/monitors', headers=headers, json={'name':'fixture','source_id':'shop','field':'price','operator':'lt','expected':10}).json()
    result = client.post('/v1/monitors/evaluate', headers=headers, json={'source_id':'shop','event':{'price':5}})
    assert result.status_code == 200, result.text
    assert result.json()['fired'] == [created['id']]
    assert client.get('/v1/monitors', headers=headers).json()['monitors'][0]['fire_count'] == 1


def test_monitor_feed_requires_write_scope_and_bounds_event(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'monitors', MonitorStore(tmp_path / 'm.db'))
    client = TestClient(api.app)
    data = {'source_id': 'shop', 'event': {'price': 5}}
    assert client.post('/v1/monitors/evaluate', json=data).status_code == 401
    _, token = api.tokens.create('fixture-read', {'jobs:read'})
    assert client.post('/v1/monitors/evaluate', headers={'Authorization': f'Bearer {token}'}, json=data).status_code == 403
    headers = {'Authorization': 'Bearer test-bootstrap-token'}
    huge = {'source_id': 'shop', 'event': {'body': 'x' * 32769}}
    assert client.post('/v1/monitors/evaluate', headers=headers, json=huge).status_code == 422
    assert api.monitors.list('bootstrap') == []

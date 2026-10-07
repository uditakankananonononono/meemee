"""Endpoint reachability is not evidence that the requested model is served."""
import respx
from fastapi.testclient import TestClient
from httpx import Response

from meemee import api


def test_models_status_never_names_unlisted_model_as_serving(monkeypatch):
    monkeypatch.setattr(api.settings,'model_routes','agent=local;chat=local;reflection=local')
    monkeypatch.setattr(api.settings,'model_name','requested-model')
    monkeypatch.setattr(api.settings,'model_base_url','https://fixture.invalid/v1')
    with respx.mock:
        respx.get('https://fixture.invalid/v1/models').mock(return_value=Response(200,json={'data':[{'id':'different-model'}]}))
        body=TestClient(api.app).get('/v1/models/status?probe=true',headers={'Authorization':'Bearer test-bootstrap-token'}).json()
    assert body['probes']['local']['reachable'] is True
    assert body['probes']['local']['model_listed'] is False
    assert all(value is None for value in body['serving'].values()), body['serving']

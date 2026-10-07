"""Malformed listings must produce per-profile failures, not abort status."""
import httpx
import pytest

from meemee.config import Settings
from meemee.model_profiles import ModelCatalog, probe_profile


@pytest.mark.parametrize('payload', [[], {'data': None}, {'data': 'not a listing'}])
async def test_malformed_listing_is_reported_as_probe_failure(payload):
    settings = Settings(model_base_url='https://fixture.invalid/v1', model_name='model')
    profile = ModelCatalog.from_settings(settings).profiles['local']
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        result = await probe_profile(profile, client=client)
    assert result['reachable'] is False
    assert 'error' in result

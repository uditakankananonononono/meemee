"""A required model endpoint is not healthy when its request is rejected."""

import httpx
import pytest

from meemee.health import ReadinessChecker


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 404, 429, 302])
async def test_required_model_http_rejection_is_not_ready(tmp_path, status):
    checker = ReadinessChecker(tmp_path, {}, 'https://model.test', min_free_bytes=1, require_model=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        result = await checker.check(client)
    assert result['components']['model'] == {'ok': False, 'status': status}
    assert result['status'] == 'not_ready'


@pytest.mark.asyncio
async def test_optional_failed_model_does_not_gate_database_readiness(tmp_path):
    checker = ReadinessChecker(tmp_path, {}, 'https://model.test', min_free_bytes=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        result = await checker.check(client)
    assert result['status'] == 'ready'
    assert result['components']['model']['ok'] is False

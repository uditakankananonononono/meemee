"""Readiness handles explicit failed probes and native driver errors."""

import sqlite3

import httpx
import pytest

from meemee.health import ReadinessChecker


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['false', 'sqlite'])
async def test_database_probe_failure_is_not_ready(tmp_path, failure):
    def probe():
        if failure == 'sqlite':
            raise sqlite3.OperationalError('private connection detail')
        return False

    checker = ReadinessChecker(tmp_path, {'db': probe}, 'https://model.test', min_free_bytes=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        result = await checker.check(client)
    assert result['status'] == 'not_ready'
    assert result['components']['db']['ok'] is False
    assert 'private connection detail' not in str(result)

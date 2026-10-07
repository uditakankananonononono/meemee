"""PostgreSQL preflight errors are contained and never echo connection secrets."""

import httpx
import pytest

from meemee.config import Settings
from meemee.preflight import run_preflight


@pytest.mark.asyncio
@pytest.mark.parametrize('driver_error', [False, True])
async def test_pg_connection_failure_does_not_escape_or_leak(tmp_path, monkeypatch, driver_error):
    from psycopg import OperationalError

    secret = 'postgresql://owner:private-password@db.example/app'

    def broken(*args, **kwargs):
        error_type = OperationalError if driver_error else RuntimeError
        raise error_type('connection rejected: ' + secret)

    monkeypatch.setattr('meemee_persist_pg.Database', broken)
    settings = Settings(data_dir=tmp_path, persistence_backend='postgresql', postgres_dsn=secret)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        result = await run_preflight(settings, client)
    check = next(row for row in result['checks'] if row['name'] == 'postgresql_connection_and_schema')
    assert check['ok'] is False
    assert check['error'] == ('OperationalError' if driver_error else 'RuntimeError')
    assert 'private-password' not in str(result)

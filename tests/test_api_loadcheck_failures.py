"""ASGI application errors are failed requests, not an aborted loadcheck."""

import pytest

from meemee.api_loadcheck import run_api_loadcheck


@pytest.mark.asyncio
async def test_loadcheck_reports_application_exception_without_details():
    async def broken(scope, receive, send):
        raise RuntimeError('private fixture detail')

    result = await run_api_loadcheck(broken, 'dummy', requests=6, concurrency=2)
    assert result['status'] == 'fail'
    assert result['completed'] == 0
    assert result['errors'] == ['RuntimeError'] * 6
    assert 'private fixture detail' not in str(result)


@pytest.mark.asyncio
async def test_loadcheck_preserves_cancellation():
    import asyncio

    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_api_loadcheck(cancelled, 'dummy', requests=1, concurrency=1)

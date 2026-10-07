"""A functional API load gate cannot pass rejected authenticated routes."""

import pytest

from meemee.api_loadcheck import run_api_loadcheck


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 403, 404, 302])
async def test_loadcheck_rejected_http_status_fails(code):
    async def rejected(scope, receive, send):
        status = 200 if scope['path'] == '/health' else code
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'x-request-id', b'fixture')]})
        await send({'type': 'http.response.body', 'body': b'{}'})

    result = await run_api_loadcheck(rejected, 'dummy', requests=3, concurrency=1)
    assert result['status'] == 'fail'
    assert result['completed'] == 3
    assert result['unexpected_statuses'] == {str(code): 2}

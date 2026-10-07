"""Arbitrary HTTP method strings cannot create new metrics series."""

import httpx
import pytest
from starlette.applications import Starlette

from meemee.observability import HTTP_REQUESTS, MetricsMiddleware


@pytest.mark.asyncio
async def test_unknown_methods_use_bounded_metric_label():
    app = Starlette()
    app.add_middleware(MetricsMiddleware)
    methods = ['FIXTUREMETHODA', 'FIXTUREMETHODB']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for method in methods:
            await client.request(method, '/missing')
    labels = {sample.labels['method'] for metric in HTTP_REQUESTS.collect() for sample in metric.samples
              if sample.name == 'meemee_http_requests_total'}
    assert not labels.intersection(methods)
    assert '__other__' in labels

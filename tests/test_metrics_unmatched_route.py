"""Unmatched request paths cannot create unbounded per-path metric labels."""

import httpx
import pytest
from starlette.applications import Starlette

from meemee.observability import HTTP_REQUESTS, MetricsMiddleware


@pytest.mark.asyncio
async def test_unmatched_paths_use_one_metric_route_label():
    app = Starlette()
    app.add_middleware(MetricsMiddleware)
    paths = ['/missing-cardinality-fixture-a', '/missing-cardinality-fixture-b']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for path in paths:
            assert (await client.get(path)).status_code == 404
    routes = {sample.labels['route'] for metric in HTTP_REQUESTS.collect() for sample in metric.samples
              if sample.name == 'meemee_http_requests_total'}
    assert not routes.intersection(paths)
    assert '__unmatched__' in routes

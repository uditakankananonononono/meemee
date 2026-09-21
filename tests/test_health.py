from pathlib import Path

import httpx
import pytest

from meemee.health import ReadinessChecker


@pytest.mark.asyncio
async def test_readiness_reports_components(tmp_path: Path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r)))
    checker = ReadinessChecker(tmp_path, {"db": lambda: 1}, "https://model.test", min_free_bytes=1)
    result = await checker.check(client)
    assert result["status"] == "ready"
    assert result["components"]["db"]["ok"] and result["components"]["model"]["ok"]


@pytest.mark.asyncio
async def test_readiness_fails_closed_without_leaking_error(tmp_path: Path):
    def broken(): raise RuntimeError("secret database details")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, request=r)))
    checker = ReadinessChecker(tmp_path, {"db": broken}, "https://model.test", min_free_bytes=10**30)
    result = await checker.check(client)
    assert result["status"] == "not_ready"
    assert result["components"]["db"]["error"] == "RuntimeError"
    assert "secret" not in str(result)

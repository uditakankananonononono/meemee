import pytest

from meemee.api import app
from meemee.api_loadcheck import run_api_loadcheck


@pytest.mark.asyncio
async def test_api_loadcheck_has_request_ids_and_no_5xx():
    report=await run_api_loadcheck(app,"test-bootstrap-token",requests=90,concurrency=12)
    assert report["status"]=="pass" and report["completed"]==90
    assert report["unexpected_5xx"]==0 and report["missing_request_ids"]==0
    assert set(report["statuses"]) <= {"200","429"}


@pytest.mark.asyncio
async def test_api_loadcheck_rejects_invalid_counts():
    with pytest.raises(ValueError): await run_api_loadcheck(app,"x",requests=0)

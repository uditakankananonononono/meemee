"""Job schedule instants must not depend on API/database host local timezone."""

from datetime import datetime

import pytest
from test_token_audit_backends import persistence  # noqa: F401


def test_job_store_rejects_naive_schedule(persistence):  # noqa: F811
    with pytest.raises(ValueError, match='timezone'):
        persistence.jobs.enqueue('work', datetime(2026, 10, 8, 9))  # noqa: DTZ001


def test_api_rejects_naive_job_before_quota_or_enqueue(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from meemee import api

    calls = []
    monkeypatch.setattr(api.quotas, 'consume_job', lambda owner: calls.append('quota'))
    monkeypatch.setattr(api.jobs, 'enqueue', lambda *a, **kw: calls.append('enqueue'))
    with pytest.raises(HTTPException) as exc:
        api.create_job(api.JobRequest(goal='work', run_at='2026-10-08T09:00:00'), SimpleNamespace(id='owner'), None)
    assert exc.value.status_code == 422
    assert calls == []

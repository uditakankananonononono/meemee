"""Model parsing against the exact server payload shapes."""
from __future__ import annotations

import pytest
from conftest import NOW, job_payload

from meemee_client import (
    AuditEntry,
    AuditPage,
    CreatedToken,
    Job,
    JobEvent,
    JobStatus,
    KNOWN_SCOPES,
    RateLimitInfo,
    RunReport,
)


def test_run_report_parses_server_shape() -> None:
    report = RunReport.model_validate({
        "run_id": "abc123",
        "goal": "Find agent frameworks",
        "final": "Done: compared 4 frameworks",
        "steps_used": 5,
        "tool_results": [{"tool": "github_search", "ok": True}],
    })
    assert report.run_id == "abc123"
    assert report.steps_used == 5
    assert report.tool_results[0]["tool"] == "github_search"


def test_job_result_is_stored_as_json_string_and_decoded_on_demand() -> None:
    job = Job.model_validate(job_payload("done", result={"summary": "three papers"}))
    assert job.status is JobStatus.DONE
    assert job.is_terminal
    assert isinstance(job.result, str)
    assert job.result_data == {"summary": "three papers"}


def test_job_without_result_decodes_to_none() -> None:
    job = Job.model_validate(job_payload("queued"))
    assert job.result is None
    assert job.result_data is None
    assert not job.is_terminal


def test_job_corrupt_result_string_raises_instead_of_hiding() -> None:
    payload = job_payload("done")
    payload["result"] = "{not json"
    job = Job.model_validate(payload)
    with pytest.raises(ValueError):
        _ = job.result_data


@pytest.mark.parametrize("status,terminal", [
    ("queued", False),
    ("running", False),
    ("cancel_requested", False),
    ("done", True),
    ("failed", True),
    ("cancelled", True),
])
def test_terminal_status_classification(status: str, terminal: bool) -> None:
    job = Job.model_validate(job_payload(status))
    assert job.is_terminal is terminal


def test_job_unknown_status_is_a_contract_violation() -> None:
    with pytest.raises(ValueError):
        Job.model_validate(job_payload("exploded"))


def test_job_event_parses_durable_log_shape() -> None:
    event = JobEvent.model_validate({
        "sequence": 7,
        "job_id": "job1",
        "kind": "running",
        "payload": {"attempt": 2},
        "created_at": NOW,
    })
    assert event.sequence == 7
    assert event.payload["attempt"] == 2
    assert event.created_at.tzinfo is not None


def test_created_token_carries_one_time_warning() -> None:
    token = CreatedToken.model_validate({"id": "t1", "token": "mee_secret", "warning": "shown once; store it securely"})
    assert token.token.startswith("mee_")


def test_audit_page_verified_entries() -> None:
    page = AuditPage.model_validate({
        "verified": True,
        "entries": [{
            "sequence": 3,
            "occurred_at": NOW,
            "actor_id": "api",
            "action": "job.create",
            "resource": "job1",
            "outcome": "success",
            "metadata": {"scheduled": False},
            "previous_hash": "0" * 64,
            "entry_hash": "f" * 64,
        }],
    })
    assert page.verified
    entry: AuditEntry = page.entries[0]
    assert entry.previous_hash == "0" * 64
    assert entry.metadata == {"scheduled": False}


def test_rate_limit_info_from_full_headers() -> None:
    info = RateLimitInfo.from_headers({
        "RateLimit-Limit": "60",
        "RateLimit-Remaining": "41",
        "RateLimit-Reset": "1793000000",
    })
    assert info is not None
    assert info.limit == 60
    assert info.remaining == 41
    assert info.reset is not None and info.reset.timestamp() == 1793000000


def test_rate_limit_info_absent_on_exempt_paths() -> None:
    assert RateLimitInfo.from_headers({}) is None


def test_known_scopes_matches_server_allowlist() -> None:
    assert KNOWN_SCOPES == frozenset({"admin", "runs:write", "jobs:read", "jobs:write"})

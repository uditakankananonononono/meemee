"""Endpoint behaviour: request shapes, response parsing, client-side validation."""
from __future__ import annotations

import httpx
import pytest
from conftest import BASE_URL, JOB_ID, NOW, event_dict, job_payload, make_client

from meemee_client import JobStatus, MeemeeClient


def test_health_unauthenticated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"status": "ok", "version": "0.16.0"})

    client = make_client(handler, auth=None)
    health = client.health()
    assert health.status == "ok"
    assert health.version == "0.16.0"


def test_ready() -> None:
    client = make_client(lambda r: httpx.Response(200, json={"status": "ready"}))
    assert client.ready().status == "ready"


def test_bearer_token_is_attached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer mee_testtoken"
        assert request.headers["User-Agent"].startswith("meemee-client/")
        return httpx.Response(200, json={"status": "ok", "version": "0.16.0"})

    make_client(handler).health()


def test_runs_create_posts_goal_and_parses_report() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/runs"
        assert httpx.QueryParams(request.url.params) == httpx.QueryParams()
        import json
        assert json.loads(request.content) == {"goal": "Find frameworks", "approve_writes": True}
        return httpx.Response(200, json={
            "run_id": "run1", "goal": "Find frameworks", "final": "done",
            "steps_used": 3, "tool_results": [],
        })

    report = make_client(handler).runs.create("Find frameworks", approve_writes=True)
    assert report.run_id == "run1"
    assert report.steps_used == 3


def test_runs_create_validates_goal_length() -> None:
    client = make_client(lambda r: httpx.Response(500))
    with pytest.raises(ValueError, match="goal"):
        client.runs.create("x")
    with pytest.raises(ValueError, match="goal"):
        client.runs.create("y" * 20_001)


def test_jobs_create_with_schedule() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"goal": "Summarise arXiv", "run_at": "2026-09-22T06:00:00Z"}
        return httpx.Response(200, json={"id": JOB_ID})

    created = make_client(handler).jobs.create("Summarise arXiv", run_at="2026-09-22T06:00:00Z")
    assert created.id == JOB_ID


def test_jobs_create_with_datetime_run_at() -> None:
    import json
    from datetime import datetime, timezone

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["run_at"].startswith("2026-09-22T06:00:00")
        return httpx.Response(200, json={"id": JOB_ID})

    make_client(handler).jobs.create("goal ok", run_at=datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc))


def test_jobs_create_rejects_bad_run_at_before_hitting_the_network() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(ValueError, match="ISO 8601"):
        make_client(handler).jobs.create("goal ok", run_at="next tuesday")
    assert calls == []


def test_jobs_get_parses_full_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/jobs/{JOB_ID}"
        return httpx.Response(200, json=job_payload("running"))

    job = make_client(handler).jobs.get(JOB_ID)
    assert job.status is JobStatus.RUNNING
    assert job.max_attempts == 3


def test_jobs_cancel_running_returns_cancel_requested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(200, json={"id": JOB_ID, "status": "cancel_requested"})

    result = make_client(handler).jobs.cancel(JOB_ID)
    assert result.status is JobStatus.CANCEL_REQUESTED


def test_jobs_cancel_of_cancelled_job_is_idempotent() -> None:
    # Live-verified server semantics: re-cancelling a cancelled job returns
    # 200 with its current status; 409 is only for done/failed jobs.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": JOB_ID, "status": "cancelled"})

    result = make_client(handler).jobs.cancel(JOB_ID)
    assert result.status is JobStatus.CANCELLED


def test_jobs_events_follows_cursor_param() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "4"
        return httpx.Response(200, json={"events": [event_dict(5, "done", {"result": {}})]})

    events = make_client(handler).jobs.events(JOB_ID, after=4)
    assert [e.kind for e in events] == ["done"]


def test_jobs_iter_events_drains_until_empty() -> None:
    pages = {
        0: [event_dict(1, "queued"), event_dict(2, "running")],
        2: [event_dict(3, "done")],
        3: [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params["after"])
        return httpx.Response(200, json={"events": pages[after]})

    kinds = [e.kind for e in make_client(handler).jobs.iter_events(JOB_ID)]
    assert kinds == ["queued", "running", "done"]


def test_jobs_wait_polls_until_terminal(sleeper) -> None:
    states = iter(["queued", "running", "done"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=job_payload(next(states)))

    job = make_client(handler, sleeper=sleeper).jobs.wait(JOB_ID, poll_interval=0.5)
    assert job.status is JobStatus.DONE
    assert sleeper.calls == [0.5, 0.5]


def test_jobs_wait_times_out(sleeper) -> None:
    from meemee_client import WaitTimeoutError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=job_payload("running"))

    # Monotonic clock is real here, so give it a zero-ish timeout.
    with pytest.raises(WaitTimeoutError, match="still running"):
        make_client(handler, sleeper=sleeper).jobs.wait(JOB_ID, timeout=0.001, poll_interval=0.5)


def test_tokens_create_posts_sorted_scopes() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"name": "ci-worker", "scopes": ["jobs:read", "jobs:write"], "expires_at": "2026-12-31T00:00:00+00:00"}
        return httpx.Response(200, json={"id": "tok1", "token": "mee_raw", "warning": "shown once; store it securely"})

    token = make_client(handler).tokens.create(
        "ci-worker", {"jobs:write", "jobs:read"}, expires_at="2026-12-31T00:00:00+00:00"
    )
    assert token.token == "mee_raw"


def test_tokens_create_rejects_unknown_scopes_locally() -> None:
    client = make_client(lambda r: httpx.Response(500))
    with pytest.raises(ValueError, match="unknown scopes"):
        client.tokens.create("bad", {"jobs:read", "owner"})
    with pytest.raises(ValueError, match="at least one scope"):
        client.tokens.create("bad", set())
    with pytest.raises(ValueError, match="name"):
        client.tokens.create("", {"admin"})


def test_tokens_revoke() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v1/tokens/tok1"
        return httpx.Response(200, json={"id": "tok1", "revoked": True})

    assert make_client(handler).tokens.revoke("tok1").revoked is True


def test_audit_list_params_and_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "10"
        assert request.url.params["limit"] == "50"
        return httpx.Response(200, json={"verified": True, "entries": [{
            "sequence": 11, "occurred_at": NOW, "actor_id": "api", "action": "token.create",
            "resource": "tok1", "outcome": "success", "metadata": {"name": "ci"},
            "previous_hash": "a" * 64, "entry_hash": "b" * 64,
        }]})

    page = make_client(handler).audit.list(after=10, limit=50)
    assert page.verified
    assert page.entries[0].action == "token.create"


def test_audit_iter_entries_walks_chain() -> None:
    def entry(seq: int) -> dict:
        return {
            "sequence": seq, "occurred_at": NOW, "actor_id": "api", "action": "job.create",
            "resource": f"job{seq}", "outcome": "success", "metadata": {},
            "previous_hash": "p", "entry_hash": "h",
        }

    pages = {0: [entry(1), entry(2)], 2: [entry(3)], 3: []}

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params["after"])
        return httpx.Response(200, json={"verified": True, "entries": pages[after]})

    seqs = [e.sequence for e in make_client(handler).audit.iter_entries(limit=2)]
    assert seqs == [1, 2, 3]


def test_audit_limit_bounds() -> None:
    client = make_client(lambda r: httpx.Response(500))
    with pytest.raises(ValueError):
        client.audit.list(limit=0)
    with pytest.raises(ValueError):
        client.audit.list(limit=501)


def test_metrics_returns_prometheus_text() -> None:
    body = "# HELP meemee_requests_total\nmeemee_requests_total 42\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/metrics"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/plain; version=0.0.4"})

    assert "meemee_requests_total 42" in make_client(handler).metrics()


def test_client_rejects_bad_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        MeemeeClient("127.0.0.1:8787")


def test_context_manager_closes() -> None:
    with make_client(lambda r: httpx.Response(200, json={"status": "ok", "version": "0.16.0"})) as client:
        assert client.health().status == "ok"

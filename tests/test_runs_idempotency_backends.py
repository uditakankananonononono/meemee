"""Completed run history and idempotency records: one contract, SQLite and PostgreSQL."""
import time

import pytest
from test_token_audit_backends import persistence  # noqa: F401 - shared two-backend fixture

from meemee.idempotency import IdempotencyConflict
from meemee.types import ApprovalRefusal, RunReport
from meemee_persist_pg.interfaces import IdempotencyStoreInterface, RunStoreInterface


def _report(run_id, goal="g", blocked=False):
    approvals = [ApprovalRefusal(step=1, tool="github.create_issue", risk="write", reason="approval_required",
                                 detail="needs approval", arguments={"repo": "a/b"}, grantable=True,
                                 persistent_grant={"tool": "github.create_issue"})] if blocked else []
    return RunReport(run_id=run_id, goal=goal, final=f"done {run_id}", steps_used=2,
                     tool_results=[{"tool": "workspace.read_file", "ok": True, "output": "caf\u00e9 \u2603"}],
                     approvals_required=approvals)


def test_stores_match_interfaces(persistence):  # noqa: F811
    assert isinstance(persistence.runs, RunStoreInterface) and persistence.runs.ping()
    assert isinstance(persistence.idempotency, IdempotencyStoreInterface) and persistence.idempotency.ping()


def test_run_add_get_is_owner_scoped_and_round_trips(persistence):  # noqa: F811
    runs = persistence.runs
    runs.add("alice", _report("r1", blocked=True))
    got = runs.get("alice", "r1")
    assert got["goal"] == "g" and got["final"] == "done r1" and got["steps_used"] == 2
    assert got["tool_results"] == [{"tool": "workspace.read_file", "ok": True, "output": "caf\u00e9 \u2603"}]
    assert got["blocked"] is True and got["approvals_required"][0]["tool"] == "github.create_issue"
    assert got["created_at"].endswith("+00:00")
    assert runs.get("bob", "r1") is None and runs.get("alice", "nope") is None
    with pytest.raises(Exception):  # noqa: B017 - duplicate run ids are rejected on both backends
        runs.add("alice", _report("r1"))


def test_run_list_orders_newest_first_with_cursor_paging(persistence):  # noqa: F811
    runs = persistence.runs
    for i in range(7):
        runs.add("pager", _report(f"run-{i}"))
        time.sleep(0.002)
    runs.add("someone-else", _report("other"))
    seen, cursor = [], None
    while True:
        page, cursor = runs.list("pager", limit=3, cursor=cursor)
        seen.extend(r["run_id"] for r in page)
        if cursor is None:
            break
    assert seen == [f"run-{i}" for i in reversed(range(7))]
    newest = runs.list("pager", limit=1)[0][0]
    older, _ = runs.list("pager", before=newest["created_at"])
    assert [r["run_id"] for r in older] == [f"run-{i}" for i in reversed(range(6))]
    assert sorted(runs.run_ids("pager")) == sorted(f"run-{i}" for i in range(7))
    assert runs.delete_principal("pager") == 7 and runs.list("pager")[0] == []
    with pytest.raises(ValueError):
        runs.delete_principal("")


def test_idempotency_replays_same_request_and_rejects_different_one(persistence):  # noqa: F811
    idem = persistence.idempotency
    payload = {"goal": "ship", "run_at": None}
    assert idem.get("p", "/v1/jobs", "k1", payload) is None
    idem.put("p", "/v1/jobs", "k1", payload, 200, {"id": "job-1", "quota": {"used": 1}})
    assert idem.get("p", "/v1/jobs", "k1", {"run_at": None, "goal": "ship"}) == (200, {"id": "job-1", "quota": {"used": 1}})
    with pytest.raises(IdempotencyConflict):
        idem.get("p", "/v1/jobs", "k1", {"goal": "other"})
    assert idem.get("q", "/v1/jobs", "k1", payload) is None  # keys are per principal
    with pytest.raises(ValueError):
        idem.put("p", "/v1/jobs", "", payload, 200, {})
    assert idem.delete_principal("p") == 1 and idem.get("p", "/v1/jobs", "k1", payload) is None


def test_idempotency_records_expire(persistence, tmp_path):  # noqa: F811
    idem = persistence.idempotency
    idem.ttl = idem.ttl * 0 - idem.ttl  # already expired when written
    idem.put("p", "/v1/jobs", "old", {"a": 1}, 200, {"id": "x"})
    assert idem.get("p", "/v1/jobs", "old", {"a": 1}) is None


def test_idempotency_claim_is_exclusive_and_release_frees_the_key(persistence):  # noqa: F811
    from meemee.idempotency import IdempotencyConflict, IdempotencyInProgress
    store = persistence.idempotency
    assert store.claim("alice", "/v1/jobs", "c1", {"goal": "x"}) is None
    with pytest.raises(IdempotencyInProgress):
        store.claim("alice", "/v1/jobs", "c1", {"goal": "x"})
    with pytest.raises(IdempotencyConflict):
        store.claim("alice", "/v1/jobs", "c1", {"goal": "y"})
    store.release("alice", "/v1/jobs", "c1")
    assert store.claim("alice", "/v1/jobs", "c1", {"goal": "x"}) is None
    store.put("alice", "/v1/jobs", "c1", {"goal": "x"}, 200, {"id": "job-1"})
    assert store.claim("alice", "/v1/jobs", "c1", {"goal": "x"}) == (200, {"id": "job-1"})
    store.release("alice", "/v1/jobs", "c1")  # a finished record is never released
    assert store.get("alice", "/v1/jobs", "c1", {"goal": "x"}) == (200, {"id": "job-1"})
    store.put("alice", "/v1/jobs", "c1", {"goal": "x"}, 200, {"id": "job-2"})  # nor overwritten
    assert store.get("alice", "/v1/jobs", "c1", {"goal": "x"}) == (200, {"id": "job-1"})


def test_idempotency_claim_races_have_one_winner(persistence):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor

    from meemee.idempotency import IdempotencyInProgress
    store = persistence.idempotency

    def attempt(_):
        try:
            return store.claim("bob", "/v1/jobs", "race", {"goal": "x"})
        except IdempotencyInProgress:
            return "busy"
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(16)))
    assert results.count(None) == 1 and results.count("busy") == 15


def test_retention_run_refuses_postgresql_mode(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from meemee.cli import app

    monkeypatch.setenv("MEEMEE_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["retention-run"])
    assert result.exit_code == 2 and "SQLite data directories only" in result.output
    assert not (tmp_path / "runs.sqlite3").exists()

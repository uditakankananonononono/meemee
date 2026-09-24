"""Live PostgreSQL coverage for every meemee_persist_pg store.

Set MEEMEE_TEST_POSTGRES_DSN (or MEEMEE_TEST_DATABASE_URL) to a server where the role may
CREATE DATABASE. Each test gets its own throwaway database, dropped afterwards.
`python scripts/pg_live_check.py` starts a local server and runs this for you.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires real PostgreSQL")


@pytest.fixture
def database():
    import psycopg
    from psycopg.conninfo import make_conninfo

    from meemee_persist_pg import Database, MigrationStore

    name = "meemee_live_" + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    value = Database(make_conninfo(DSN, dbname=name), min_size=1, max_size=12)
    MigrationStore(value).apply()
    try:
        yield value
    finally:
        value.close()
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_migrations_are_idempotent_and_recorded(database):
    from meemee_persist_pg import MigrationStore

    assert MigrationStore(database).apply() == []
    with database.transaction() as c:
        tables = {r["table_name"] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    assert {"meemee_jobs", "meemee_job_events", "meemee_audit_log", "meemee_api_tokens",
            "meemee_accounts", "meemee_plans"} <= tables


def test_memory_full_text_and_recent(database):
    from meemee_persist_pg import MemoryStore

    memory = MemoryStore(database)
    first = memory.add("run-1", "fact", "Udita prefers green tea in the morning", {"src": "chat"})
    memory.add("run-1", "fact", "The build server runs Ubuntu", None)
    hits = memory.search("tea")
    assert hits and hits[0]["id"] == first
    assert [row["id"] for row in memory.recent(1)] != []
    assert memory.search("nonexistentword") == []


def test_tokens_expiry_and_revocation(database):
    from meemee_persist_pg import TokenStore

    tokens = TokenStore(database)
    ident, raw = tokens.create("ops", {"jobs:read", "jobs:write"})
    principal = tokens.authenticate(raw)
    assert principal.id == ident
    assert tokens.authenticate(raw + "x") is None
    _, expired = tokens.create("old", {"jobs:read"}, expires_at="2000-01-01T00:00:00+00:00")
    assert tokens.authenticate(expired) is None
    assert tokens.revoke(ident) and tokens.authenticate(raw) is None


def test_audit_chain_detects_tampering(database):
    from meemee_persist_pg import AuditLog

    audit = AuditLog(database)
    for index in range(5):
        audit.append("actor", "write", f"res-{index}", "success", {"n": index})
    ok, bad = audit.verify()
    if not ok:
        with database.transaction() as c:
            tz = c.execute("SHOW TimeZone").fetchone()["TimeZone"]
        pytest.fail(f"untampered audit chain failed verification at row {bad} (server TimeZone={tz}); "
                    "verify must compare hashes in UTC regardless of server TimeZone")
    with database.transaction() as c:
        c.execute("UPDATE meemee_audit_log SET resource='forged' WHERE sequence=3")
    assert audit.verify() == (False, 3)


def test_job_lifecycle_retry_and_events(database):
    from meemee_persist_pg import JobStore

    store = JobStore(database, worker_id="w1")
    ident = store.enqueue("retry me", max_attempts=2)
    job = store.claim()
    store.fail(ident, "transient", job["lease_token"])
    assert store.get(ident)["status"] == "queued"
    job = store.claim()
    assert job["attempts"] == 2
    store.fail(ident, "permanent", job["lease_token"])
    assert store.get(ident)["status"] == "failed"
    kinds = [event["kind"] for event in store.events(ident)]
    assert kinds == ["queued", "running", "retry", "running", "failed"]


def test_concurrent_claims_never_double_claim(database):
    from meemee_persist_pg import JobStore

    producer = JobStore(database, worker_id="producer")
    ids = {producer.enqueue(f"job {index}") for index in range(60)}
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        store = JobStore(database, worker_id=name)
        while True:
            job = store.claim()
            if job is None:
                return
            with lock:
                claimed.append(job["id"])
            store.finish(job["id"], {"by": name}, job["lease_token"])

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert sorted(claimed) == sorted(ids)
    assert len(claimed) == len(set(claimed))
    assert all(producer.get(ident)["status"] == "done" for ident in ids)


def test_expired_lease_is_reaped_and_fenced(database):
    from meemee_persist_pg import JobStore, LeaseLostError

    dead = JobStore(database, worker_id="dead", lease_seconds=1)
    ident = dead.enqueue("lease test", max_attempts=3)
    stale = dead.claim()
    time.sleep(1.3)
    fresh = JobStore(database, worker_id="alive").claim()
    assert fresh["id"] == ident and fresh["attempts"] == 2
    assert dead.heartbeat(ident, stale["lease_token"]) is False
    with pytest.raises(LeaseLostError):
        dead.finish(ident, {"late": True}, stale["lease_token"])


def test_cancel_queued_and_running(database):
    from meemee_persist_pg import JobStore

    store = JobStore(database, worker_id="w")
    queued = store.enqueue("never runs")
    assert store.cancel(queued) and store.get(queued)["status"] == "cancelled"
    running = store.enqueue("runs")
    job = store.claim()
    assert store.request_cancel(running) == "cancel_requested"
    assert store.cancel_running(running, job["lease_token"])
    assert store.get(running)["status"] == "cancelled"


def test_cancel_requested_job_with_dead_worker_is_not_stranded(database):
    from meemee_persist_pg import JobStore

    dead = JobStore(database, worker_id="dead", lease_seconds=1)
    ident = dead.enqueue("cancel while worker dies")
    dead.claim()
    assert dead.request_cancel(ident) == "cancel_requested"
    time.sleep(1.3)
    JobStore(database, worker_id="other").claim()  # any claim runs the reaper
    assert dead.get(ident)["status"] == "cancelled"


def test_principal_listing_pagination(database):
    from meemee_persist_pg import JobStore

    store = JobStore(database, worker_id="w")
    mine = {store.enqueue(f"mine {index}", principal="acct_a") for index in range(7)}
    store.enqueue("theirs", principal="acct_b")
    seen, cursor = [], None
    while True:
        items, cursor = store.list_for_principal("acct_a", limit=3, cursor=cursor)
        seen.extend(item["id"] for item in items)
        if cursor is None:
            break
    assert sorted(seen) == sorted(mine) and len(seen) == len(set(seen))
    assert store.get_owned(next(iter(mine)), "acct_b") is None


def test_plan_store_versioning(database):
    from meemee.types import Plan, PlanStep
    from meemee_persist_pg import PlanStore

    plans = PlanStore(database)
    plan = Plan(goal="ship", steps=[PlanStep(id="a", description="write"),
                                    PlanStep(id="b", description="test", depends_on=["a"])])
    created = plans.create(plan)
    assert created["version"] == 1 and created["plan"].steps[0].id == "a"
    updated = plans.update_status(created["id"], "a", "done", 1)
    assert updated["version"] == 2 and updated["plan"].steps[0].status == "done"
    with pytest.raises(ValueError, match="version conflict"):
        plans.update_status(created["id"], "b", "done", 1)
    assert [row["version"] for row in plans.history(created["id"])] == [1, 2]


def test_shared_rate_limiter_is_atomic(database):
    from meemee_persist_pg.rate_limit import PostgreSQLRateLimiter

    limiter = PostgreSQLRateLimiter(database, limit=50, window_seconds=60)
    now = 1_900_000_000.0
    allowed = []
    lock = threading.Lock()

    def hammer() -> None:
        for _ in range(20):
            ok, _, _ = limiter.hit("client", now)
            with lock:
                allowed.append(ok)

    threads = [threading.Thread(target=hammer) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert allowed.count(True) == 50 and allowed.count(False) == 50
    assert limiter.cleanup(now + 1000) == 1


def test_accounts_login_lockout_and_disable(database):
    from meemee_persist_pg import AccountStore, TokenStore

    accounts = AccountStore(database)
    account, session = accounts.create_account("Live@Example.com", "correct horse battery", "Live")
    assert account["email"] == "live@example.com"
    assert TokenStore(database).authenticate(session) is not None
    assert accounts.login_account("live@example.com", "correct horse battery") is not None
    for _ in range(5):
        assert accounts.login_account("live@example.com", "wrong password!!") is None
    assert accounts.login_account("live@example.com", "correct horse battery") is None  # locked
    assert accounts.disable_account(account["id"])
    assert TokenStore(database).authenticate(session) is None


def test_worker_can_heartbeat_while_winding_down_a_cancel(database):
    from meemee_persist_pg import JobStore

    store = JobStore(database, worker_id="w", lease_seconds=30)
    ident = store.enqueue("long job")
    job = store.claim()
    assert store.request_cancel(ident) == "cancel_requested"
    assert store.heartbeat(ident, job["lease_token"]) is True
    assert store.cancel_running(ident, job["lease_token"])
    assert store.get(ident)["status"] == "cancelled"

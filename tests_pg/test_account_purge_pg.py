"""Live PostgreSQL account purge. Run with MEEMEE_TEST_POSTGRES_DSN pointed at an expendable database."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("MEEMEE_TEST_POSTGRES_DSN"), reason="requires real PostgreSQL")


def db():
    from meemee_persist_pg import Database, MigrationStore
    value = Database(os.environ["MEEMEE_TEST_POSTGRES_DSN"], min_size=1, max_size=4)
    MigrationStore(value).apply()
    return value


def test_pg_purge_principal_idle_running_and_memory():
    from meemee_persist_pg import JobStore, MemoryStore
    value = db()
    alice, bob = f"alice-{uuid.uuid4().hex[:8]}", f"bob-{uuid.uuid4().hex[:8]}"
    word = f"okapi{uuid.uuid4().hex[:10]}"
    try:
        jobs, memory = JobStore(value, worker_id=f"w-{uuid.uuid4().hex[:6]}"), MemoryStore(value)
        # drain anything queued by other tests so claims are deterministic
        while (leftover := jobs.claim()) is not None:
            jobs.finish(leftover["id"], {"drained": True}, leftover["lease_token"])
        done = jobs.enqueue("alice finished", principal=alice)
        claimed = jobs.claim(); assert claimed["id"] == done
        jobs.finish(done, {"run_id": f"run-{alice}"}, claimed["lease_token"])
        memory.add(f"run-{alice}", "final", f"{alice} {word} memory")
        memory.add(f"run-{bob}", "final", f"{bob} {word} memory")
        queued = jobs.enqueue("alice queued", principal=alice)
        running = jobs.claim(); assert running["id"] == queued
        idle_bob = jobs.enqueue("bob keeps this", principal=bob)

        assert jobs.run_ids_for_principal(alice) == [f"run-{alice}"]
        assert memory.delete_runs(jobs.run_ids_for_principal(alice)) == 1
        counts = jobs.purge_principal(alice)
        assert counts["jobs_deleted"] == 1 and counts["running_tombstoned"] == 1 and counts["job_events_deleted"] >= 4

        tomb = jobs.get(queued)
        assert tomb["goal"] == "[deleted]" and tomb["principal"] is None and tomb["status"] == "cancel_requested"
        assert jobs.events(queued) == [] and jobs.get(done) is None
        assert jobs.list_for_principal(alice)[0] == []

        # The worker's terminal call deletes the tombstone instead of writing a result.
        assert jobs.finish(queued, {"run_id": "late"}, running["lease_token"]) is True
        assert jobs.get(queued) is None
        assert jobs.get(idle_bob)["status"] == "queued" and jobs.get(idle_bob)["principal"] == bob
        assert [m["run_id"] for m in memory.search(word)] == [f"run-{bob}"]
    finally:
        value.close()


def test_pg_failed_tombstone_is_deleted_and_expired_tombstone_is_reaped():
    from meemee_persist_pg import JobStore
    value = db()
    who = f"carol-{uuid.uuid4().hex[:8]}"
    try:
        jobs = JobStore(value, worker_id=f"w-{uuid.uuid4().hex[:6]}", lease_seconds=60)
        while (leftover := jobs.claim()) is not None:
            jobs.finish(leftover["id"], {"drained": True}, leftover["lease_token"])
        first = jobs.enqueue("retry me", principal=who, max_attempts=5)
        claim = jobs.claim(); assert claim["id"] == first
        jobs.purge_principal(who)
        assert jobs.fail(first, "boom", claim["lease_token"]) is True
        assert jobs.get(first) is None

        second = jobs.enqueue("abandon me", principal=who)
        claim = jobs.claim(); assert claim["id"] == second
        jobs.purge_principal(who)
        with value.transaction() as c:
            c.execute("UPDATE meemee_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (second,))
        assert jobs.claim() is None  # claim reaps expired tombstones instead of requeueing them
        assert jobs.get(second) is None
    finally:
        value.close()

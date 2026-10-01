"""Real SQLite connections/processes, including the read/consume interleaving."""
import multiprocessing
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from meemee.quotas import QuotaExceeded, QuotaStore

NOW = datetime(2026, 9, 24, 23, 59, tzinfo=timezone.utc)


def _consume_process(path, barrier, results):
    store = QuotaStore(path)
    try:
        barrier.wait(timeout=10)
        try:
            results.put(("accepted", store.consume_job("p", NOW)["used"]))
        except QuotaExceeded:
            results.put(("refused", None))
        assert not store.db.in_transaction
    except (AssertionError, sqlite3.Error, threading.BrokenBarrierError) as exc:
        results.put(("error", repr(exc)))
    finally:
        store.db.close()


def test_limit_lowering_cannot_commit_between_read_and_consume(tmp_path):
    path = tmp_path / "quota.db"
    reader, writer = QuotaStore(path), QuotaStore(path)
    reader.set_limit("p", 2)
    reader.consume_job("p", NOW)
    read_barrier, release = threading.Barrier(2), threading.Event()
    original_limit = reader.limit
    outcome = []

    def paused_limit(principal):
        value = original_limit(principal)
        read_barrier.wait(timeout=5)
        assert release.wait(timeout=5)
        return value

    reader.limit = paused_limit

    def consume():
        try:
            outcome.append(reader.consume_job("p", NOW))
        except (AssertionError, sqlite3.Error, threading.BrokenBarrierError) as exc:
            outcome.append(exc)

    thread = threading.Thread(target=consume)
    thread.start()
    try:
        read_barrier.wait(timeout=5)
        writer.db.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            writer.set_limit("p", 1)
        assert not writer.db.in_transaction
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], dict), outcome
    assert outcome[0]["used"] == 2
    reader.limit = original_limit
    writer.set_limit("p", 1)
    with pytest.raises(QuotaExceeded, match=r"\(1\)"):
        reader.consume_job("p", NOW)
    assert reader.status("p", NOW)["used"] == 2
    assert not reader.db.in_transaction and not writer.db.in_transaction
    print("limit read protected; lowering serialized after consume; next consume refused")


def test_limit_lowered_before_consume_is_observed(tmp_path):
    path = tmp_path / "quota.db"
    reader, writer = QuotaStore(path), QuotaStore(path)
    writer.set_limit("p", 3)
    reader.consume_job("p", NOW)
    assert reader.limit("p") == 3
    writer.set_limit("p", 1)
    with pytest.raises(QuotaExceeded):
        reader.consume_job("p", NOW)
    assert reader.status("p", NOW)["used"] == 1


def test_spawned_processes_share_daily_limit(tmp_path):
    path = tmp_path / "quota.db"
    store = QuotaStore(path)
    store.set_limit("p", 3)
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(8), ctx.Queue()
    processes = [ctx.Process(target=_consume_process, args=(path, barrier, results))
                 for _ in range(8)]
    for process in processes:
        process.start()
    outcomes = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(value for kind, value in outcomes if kind == "accepted") == [1, 2, 3]
    assert sum(kind == "refused" for kind, _ in outcomes) == 5, outcomes
    assert store.status("p", NOW)["used"] == 3
    assert store.consume_job("p", NOW + timedelta(minutes=1))["used"] == 1
    assert store.status("p", NOW)["used"] == 3
    assert not store.db.in_transaction
    print("8 spawned processes: 3 accepted, 5 refused; UTC rollover: used=1")


def test_legacy_schema_and_rows_preserved(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE quota_limits(principal TEXT PRIMARY KEY,
              daily_jobs INTEGER NOT NULL CHECK(daily_jobs>0));
            CREATE TABLE quota_usage(principal TEXT NOT NULL, day TEXT NOT NULL,
              jobs INTEGER NOT NULL, PRIMARY KEY(principal,day));
            INSERT INTO quota_limits VALUES('p', 2);
            INSERT INTO quota_usage VALUES('p', '2026-09-24', 1);
        """)
        schema = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    store = QuotaStore(path)
    assert [tuple(row) for row in store.db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")] == schema
    assert store.consume_job("p", NOW)["used"] == 2
    with pytest.raises(QuotaExceeded):
        store.consume_job("p", NOW)
    assert store.status("p", NOW)["used"] == 2
    print("legacy schema unchanged; existing rows preserved")


@pytest.mark.parametrize("operation", ["consume", "set_limit", "delete"])
def test_sql_failure_rolls_back_and_releases_writer(tmp_path, operation):
    path = tmp_path / "quota.db"
    store, other = QuotaStore(path), QuotaStore(path)
    store.set_limit("p", 2)
    store.consume_job("p", NOW)
    if operation == "consume":
        table, action = "quota_usage", "UPDATE"
        call = lambda: store.consume_job("p", NOW)
    elif operation == "set_limit":
        table, action = "quota_limits", "UPDATE"
        call = lambda: store.set_limit("p", 3)
    else:
        table, action = "quota_limits", "DELETE"
        call = lambda: store.delete_principal("p")
    store.db.execute(f"CREATE TRIGGER fail BEFORE {action} ON {table} "
                     "BEGIN SELECT RAISE(ABORT, 'injected SQL failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected SQL failure"):
        call()
    assert not store.db.in_transaction
    assert other.status("p", NOW)["used"] == 1
    other.db.execute("PRAGMA busy_timeout=0")
    other.set_limit("unrelated", 1)
    store.db.execute("DROP TRIGGER fail")
    assert other.consume_job("p", NOW)["used"] == 2
    print(f"{operation}: SQL failure rolled back; second connection acquired writer")

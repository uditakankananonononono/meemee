"""A store whose constructor fails after opening SQLite must not leak the handle (area 208)."""
import asyncio
import importlib
import sqlite3
import types

import pytest

from meemee._sqlite_guard import close_db_on_init_failure

STORES = [
    ("meemee.reflection_schedule", "ReflectionSchedule"),
    ("meemee.personal_model", "PersonalModelStore"),
    ("meemee.runs", "RunStore"),
    ("meemee.goals", "GoalStore"),
    ("meemee.memory", "MemoryStore"),
    ("meemee.plan_store", "PlanStore"),
    ("meemee.browser_notices", "TakeoverNoticeQueue"),
    ("meemee.idempotency", "IdempotencyStore"),
    ("meemee.companion.store", "CompanionStore"),
    ("meemee.auth", "TokenStore"),
    ("meemee.source_health", "SourceHealthStore"),
    ("meemee.jobs", "JobStore"),
    ("meemee.monitors", "MonitorStore"),
    ("meemee.context", "ContextStore"),
    ("meemee.browser_sessions", "BrowserSessionStore"),
]


class _Tracked:
    """Delegating connection that fails the first schema statement."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "closed", False)
        object.__setattr__(self, "failed", False)

    def _maybe_fail(self, sql):
        if not self.failed and "create table" in str(sql).lower():
            object.__setattr__(self, "failed", True)
            raise sqlite3.OperationalError("injected schema failure")

    def execute(self, sql, *a, **k):
        self._maybe_fail(sql)
        return self._conn.execute(sql, *a, **k)

    def executescript(self, sql):
        self._maybe_fail(sql)
        return self._conn.executescript(sql)

    def close(self):
        object.__setattr__(self, "closed", True)
        self._conn.close()

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


@pytest.mark.parametrize("module_name,class_name", STORES)
def test_failed_constructor_closes_sqlite_handle(tmp_path, monkeypatch, module_name, class_name):
    module = importlib.import_module(module_name)
    opened = []

    def connect(*args, **kwargs):
        tracked = _Tracked(sqlite3.connect(*args, **kwargs))
        opened.append(tracked)
        return tracked

    patched = types.SimpleNamespace(**vars(sqlite3))
    patched.connect = connect
    monkeypatch.setattr(module, "sqlite3", patched)
    with pytest.raises(sqlite3.OperationalError, match="injected schema failure"):
        getattr(module, class_name)(tmp_path / "store.sqlite3")
    assert opened, "constructor never opened SQLite"
    assert all(handle.closed for handle in opened)


class _BadCloser:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def close(self):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize(
    "close_error",
    [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(3), RuntimeError("close boom")],
)
def test_close_error_never_replaces_original_constructor_exception(close_error):
    original = ValueError("constructor failed")

    class Store:
        @close_db_on_init_failure
        def __init__(self):
            self.db = _BadCloser(close_error)
            self.connection = _BadCloser(close_error)
            raise original

    with pytest.raises(ValueError) as caught:
        Store()
    assert caught.value is original

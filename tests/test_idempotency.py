from pathlib import Path

import pytest

from meemee.idempotency import IdempotencyConflict, IdempotencyStore


def test_idempotency_replays_same_request(tmp_path: Path):
    store = IdempotencyStore(tmp_path / "i.db")
    payload = {"goal":"work"}
    assert store.get("u", "/jobs", "key", payload) is None
    store.put("u", "/jobs", "key", payload, 200, {"id":"one"})
    assert store.get("u", "/jobs", "key", payload) == (200, {"id":"one"})


def test_idempotency_rejects_key_reuse_with_changed_request(tmp_path: Path):
    store = IdempotencyStore(tmp_path / "i.db")
    store.put("u", "/jobs", "key", {"goal":"one"}, 200, {"id":"one"})
    with pytest.raises(IdempotencyConflict):
        store.get("u", "/jobs", "key", {"goal":"two"})


def test_idempotency_is_scoped_by_principal_and_route(tmp_path: Path):
    store = IdempotencyStore(tmp_path / "i.db")
    store.put("u1", "/a", "key", {}, 200, {"id":"one"})
    assert store.get("u2", "/a", "key", {}) is None
    assert store.get("u1", "/b", "key", {}) is None

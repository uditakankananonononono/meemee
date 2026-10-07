from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(secret: bytes, envelope: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    return hmac.new(secret, canonical_json(unsigned), hashlib.sha256).hexdigest()


def make_command(
    device_id: str,
    capability: str,
    arguments: dict[str, Any],
    secret: bytes,
    *,
    issued_at: int | None = None,
    nonce: str | None = None,
    command_id: str | None = None,
) -> dict[str, Any]:
    if not device_id or not capability:
        raise ValueError("device_id and capability are required")
    envelope = {
        "version": 1,
        "command_id": command_id or secrets.token_hex(16),
        "device_id": device_id,
        "capability": capability,
        "arguments": arguments,
        "issued_at": int(time.time()) if issued_at is None else int(issued_at),
        "nonce": nonce or secrets.token_hex(16),
    }
    return {**envelope, "signature": sign(secret, envelope)}


class ReplayGuard:
    def __init__(self, ttl_seconds: int = 300, clock: Callable[[], float] = time.time):
        self.ttl = ttl_seconds
        self.clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, nonce: str, *, retain_seconds: float | None = None) -> bool:
        now = self.clock()
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + max(self.ttl, retain_seconds or 0)
            return True


class SQLiteReplayGuard:
    """Optional persistent pre-execution nonce claim ledger for one peer identity."""

    def __init__(self, path: Path, namespace: str, ttl_seconds: int = 300,
                 clock: Callable[[], float] = time.time):
        if ttl_seconds < 1 or not namespace:
            raise ValueError("positive replay TTL and namespace required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl, self.clock, self.namespace = ttl_seconds, clock, namespace
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        with self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("CREATE TABLE IF NOT EXISTS device_replay(namespace TEXT NOT NULL,nonce TEXT NOT NULL,expires_at REAL NOT NULL,PRIMARY KEY(namespace,nonce))")

    def claim(self, nonce: str, *, retain_seconds: float | None = None) -> bool:
        now = self.clock()
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("DELETE FROM device_replay WHERE namespace=? AND expires_at<=?", (self.namespace, now))
            return bool(self.db.execute(
                "INSERT OR IGNORE INTO device_replay VALUES(?,?,?)",
                (self.namespace, nonce, now + max(self.ttl, retain_seconds or 0)),
            ).rowcount)

    def close(self) -> None:
        with self.lock:
            self.db.close()


@dataclass(frozen=True)
class VerifiedCommand:
    command_id: str
    device_id: str
    capability: str
    arguments: dict[str, Any]
    issued_at: int
    nonce: str


def verify_command(
    envelope: dict[str, Any],
    secret: bytes,
    *,
    expected_device_id: str | None = None,
    allowed_capabilities: set[str] | None = None,
    replay_guard: ReplayGuard | SQLiteReplayGuard | None = None,
    now: int | None = None,
    tolerance: int = 300,
) -> VerifiedCommand:
    required = {
        "version",
        "command_id",
        "device_id",
        "capability",
        "arguments",
        "issued_at",
        "nonce",
        "signature",
    }
    if (
        set(envelope) != required
        or envelope["version"] != 1
        or not isinstance(envelope["arguments"], dict)
    ):
        raise ValueError("invalid command envelope")
    supplied = envelope["signature"]
    if not isinstance(supplied, str) or not supplied.isascii():
        raise ValueError("invalid command signature")
    if not hmac.compare_digest(sign(secret, envelope), supplied):
        raise ValueError("invalid command signature")
    clock = int(time.time()) if now is None else now
    if abs(clock - int(envelope["issued_at"])) > tolerance:
        raise ValueError("stale command")
    if expected_device_id is not None and envelope["device_id"] != expected_device_id:
        raise ValueError("command addressed to another device")
    if allowed_capabilities is not None and envelope["capability"] not in allowed_capabilities:
        raise PermissionError("capability not declared by device")
    # Future-dated envelopes can remain valid for almost twice the tolerance.
    # Keep the nonce through the inclusive final accepted integer second, even
    # when the guard's configured TTL is shorter than the signature window.
    if replay_guard is not None and not replay_guard.claim(
        str(envelope["nonce"]), retain_seconds=int(envelope["issued_at"]) + tolerance - clock + 1
    ):
        raise ValueError("replayed command")
    return VerifiedCommand(
        str(envelope["command_id"]),
        str(envelope["device_id"]),
        str(envelope["capability"]),
        dict(envelope["arguments"]),
        int(envelope["issued_at"]),
        str(envelope["nonce"]),
    )

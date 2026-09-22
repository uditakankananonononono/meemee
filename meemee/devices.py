from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .device_protocol import ReplayGuard, make_command, verify_command


class DeviceRegistry:
    """SQLite pairing, manifest, command, and execution audit store."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS device_pairings(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,code_hash TEXT NOT NULL,expires_at TEXT NOT NULL,used_at TEXT);
          CREATE TABLE IF NOT EXISTS devices(device_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,name TEXT NOT NULL,secret_hex TEXT NOT NULL,manifest TEXT NOT NULL,paired_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,revoked_at TEXT);
          CREATE TABLE IF NOT EXISTS device_commands(id INTEGER PRIMARY KEY,command_id TEXT UNIQUE NOT NULL,device_id TEXT NOT NULL,capability TEXT NOT NULL,envelope TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT,result TEXT,error TEXT);
        """)

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    def create_pairing(self, owner_id: str, *, ttl_seconds: int = 300) -> dict[str, str]:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        pairing_id = secrets.token_urlsafe(18)
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO device_pairings VALUES(?,?,?,?,NULL)",
                (pairing_id, owner_id, self._hash(code), expires),
            )
        return {"pairing_id": pairing_id, "code": code, "expires_at": expires}

    def pair(
        self, pairing_id: str, code: str, *, device_id: str, name: str, capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM device_pairings WHERE id=?", (pairing_id,)
            ).fetchone()
            if (
                not row
                or row["used_at"]
                or datetime.fromisoformat(row["expires_at"]) < now
                or not hmac.compare_digest(row["code_hash"], self._hash(code))
            ):
                raise ValueError("invalid or expired pairing")
            if not device_id or not isinstance(capabilities, dict) or not capabilities:
                raise ValueError("device id and capabilities are required")
            secret = secrets.token_bytes(32)
            stamp = now.isoformat()
            manifest = json.dumps(capabilities, sort_keys=True)
            self.db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,?,?,?,NULL)",
                (device_id, row["owner_id"], name, secret.hex(), manifest, stamp, stamp),
            )
            self.db.execute("UPDATE device_pairings SET used_at=? WHERE id=?", (stamp, pairing_id))
        return {
            "device_id": device_id,
            "owner_id": row["owner_id"],
            "secret": secret.hex(),
            "capabilities": capabilities,
        }

    def get(self, owner_id: str, device_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM devices WHERE owner_id=? AND device_id=?", (owner_id, device_id)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item.pop("secret_hex")
        item["manifest"] = json.loads(item["manifest"])
        return item

    def update_manifest(
        self, owner_id: str, device_id: str, capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        if not capabilities:
            raise ValueError("capabilities cannot be empty")
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE devices SET manifest=?,last_seen_at=? WHERE owner_id=? AND device_id=? AND revoked_at IS NULL",
                (
                    json.dumps(capabilities, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                    owner_id,
                    device_id,
                ),
            ).rowcount
        if not changed:
            raise KeyError(device_id)
        return self.get(owner_id, device_id) or {}

    def issue(
        self, owner_id: str, device_id: str, capability: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM devices WHERE owner_id=? AND device_id=? AND revoked_at IS NULL",
                (owner_id, device_id),
            ).fetchone()
            if not row:
                raise KeyError(device_id)
            manifest = json.loads(row["manifest"])
            if capability not in manifest:
                raise PermissionError("capability not declared by device")
            envelope = make_command(
                device_id, capability, arguments, bytes.fromhex(row["secret_hex"])
            )
            stamp = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                "INSERT INTO device_commands(command_id,device_id,capability,envelope,status,created_at) VALUES(?,?,?,?,?,?)",
                (
                    envelope["command_id"],
                    device_id,
                    capability,
                    json.dumps(envelope, sort_keys=True),
                    "issued",
                    stamp,
                ),
            )
        return envelope

    def complete(self, command_id: str, *, result: Any = None, error: str | None = None) -> None:
        status = "failed" if error else "completed"
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE device_commands SET status=?,completed_at=?,result=?,error=? WHERE command_id=? AND status='issued'",
                (
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(result, sort_keys=True) if error is None else None,
                    error,
                    command_id,
                ),
            ).rowcount
        if not changed:
            raise ValueError("unknown or already completed command")

    def command(self, command_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM device_commands WHERE command_id=?", (command_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["envelope"] = json.loads(item["envelope"])
        item["result"] = json.loads(item["result"]) if item["result"] else None
        return item

    def revoke(self, owner_id: str, device_id: str) -> bool:
        with self.lock, self.db:
            return bool(
                self.db.execute(
                    "UPDATE devices SET revoked_at=? WHERE owner_id=? AND device_id=? AND revoked_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), owner_id, device_id),
                ).rowcount
            )


class DeviceSimulator:
    """In-process protocol peer for deterministic device integration tests."""

    def __init__(self, device_id: str, secret_hex: str, handlers: dict[str, Callable[..., Any]]):
        self.device_id = device_id
        self.secret = bytes.fromhex(secret_hex)
        self.handlers = handlers
        self.replay = ReplayGuard()

    def execute(self, envelope: dict[str, Any], *, now: int | None = None) -> Any:
        command = verify_command(
            envelope,
            self.secret,
            expected_device_id=self.device_id,
            allowed_capabilities=set(self.handlers),
            replay_guard=self.replay,
            now=now,
        )
        return self.handlers[command.capability](**command.arguments)

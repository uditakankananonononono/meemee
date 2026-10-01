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
        self.db.execute("PRAGMA secure_delete=ON")
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


class LocalDeviceStores:
    """Owner-scoped export and hard delete of the local device stores.

    ``devices.sqlite3`` (pairings, devices with their HMAC secret, issued commands with
    envelopes and results) is keyed by owner. The NativeClient journal
    ``native-device.sqlite3`` has no owner column; its rows are reached through the
    owner's device ids. A missing file or table contributes nothing.

    Delete order (each step is re-runnable after a crash):
      1. registry: revoke the owner's devices and record a progress row holding only
         hashed ``sha256(owner, device)`` keys, so later steps survive losing the rows;
      2. journal: write a purge flag per key (a live NativeClient inserts only when no
         flag newer than its device pairing exists, in the same SQL statement), then
         delete the journal rows;
      3. registry: delete commands, pairings, devices;
      4. journal sweep again (catches a command journaled between 2 and 3), including
         rows whose device is no longer in the registry but whose key the owner's purge
         flagged;
      5. VACUUM both files and truncate the WAL so freed pages hold no owner data.
    """

    REGISTRY = "devices.sqlite3"
    JOURNAL = "native-device.sqlite3"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    @staticmethod
    def purge_key(owner: str, device_id: str) -> str:
        return hashlib.sha256(f"{owner}\0{device_id}".encode()).hexdigest()

    def _open(self, name: str) -> sqlite3.Connection | None:
        path = self.data_dir / name
        if not path.exists():
            return None
        db = sqlite3.connect(path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA secure_delete=ON")
        db.create_function("purge_key", 2, self.purge_key, deterministic=True)
        return db

    @staticmethod
    def _select(db: sqlite3.Connection, query: str, args: tuple) -> list[dict]:
        try:
            return [dict(r) for r in db.execute(query, args).fetchall()]
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []

    def export_owner(self, owner: str) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {"device_pairings": [], "devices": [], "device_commands": [],
                                      "native_commands": []}
        reg = self._open(self.REGISTRY)
        ids: list[str] = []
        if reg is not None:
            try:
                out["device_pairings"] = self._select(reg, "SELECT * FROM device_pairings WHERE owner_id=? ORDER BY id", (owner,))
                out["devices"] = self._select(reg, "SELECT * FROM devices WHERE owner_id=? ORDER BY device_id", (owner,))
                ids = [r["device_id"] for r in out["devices"]]
                out["device_commands"] = self._select(
                    reg, "SELECT * FROM device_commands WHERE device_id IN (SELECT device_id FROM devices WHERE owner_id=?) ORDER BY id", (owner,))
            finally:
                reg.close()
        jr = self._open(self.JOURNAL)
        if jr is not None:
            try:
                for device_id in ids:
                    out["native_commands"] += self._select(
                        jr, "SELECT * FROM native_commands WHERE device_id=? ORDER BY command_id", (device_id,))
            finally:
                jr.close()
        return out

    _PROGRESS = ("CREATE TABLE IF NOT EXISTS purge_progress(owner_key TEXT PRIMARY KEY, keys TEXT NOT NULL, "
                 "pairings INTEGER NOT NULL DEFAULT 0, devices INTEGER NOT NULL DEFAULT 0, "
                 "commands INTEGER NOT NULL DEFAULT 0)")

    def _sweep_journal(self, jr: sqlite3.Connection, owner: str, keys: list[str]) -> set[str]:
        """Flag and delete the journal rows this owner's purge provably owns.

        Deleted-row counts are written into ``purge_flags.deleted`` in the same transaction as
        the delete, so a crash cannot lose them. Returns the flag keys touched.
        """
        jr.execute("BEGIN IMMEDIATE")
        try:
            jr.execute("CREATE TABLE IF NOT EXISTS purge_flags(key TEXT PRIMARY KEY, set_at TEXT NOT NULL)")
            if "deleted" not in {r["name"] for r in jr.execute("PRAGMA table_info(purge_flags)")}:
                jr.execute("ALTER TABLE purge_flags ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
            stamp = datetime.now(timezone.utc).isoformat()
            for key in keys:
                jr.execute("INSERT OR IGNORE INTO purge_flags(key,set_at) VALUES(?,?)", (key, stamp))
            owned = set(keys) | {r[0] for r in jr.execute("SELECT key FROM purge_flags")}
            touched = set(keys)
            try:
                ids = [r[0] for r in jr.execute("SELECT DISTINCT device_id FROM native_commands")]
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                ids = []
            for device_id in ids:
                key = self.purge_key(owner, device_id)
                if key in owned:
                    n = jr.execute("DELETE FROM native_commands WHERE device_id=?", (device_id,)).rowcount
                    jr.execute("INSERT OR IGNORE INTO purge_flags(key,set_at) VALUES(?,?)", (key, stamp))
                    jr.execute("UPDATE purge_flags SET deleted=deleted+? WHERE key=?", (n, key))
                    touched.add(key)
            jr.execute("COMMIT")
            return touched
        except Exception:
            jr.execute("ROLLBACK")
            raise

    @staticmethod
    def _journal_deleted(jr: sqlite3.Connection, keys: set[str]) -> int:
        if not keys:
            return 0
        marks = ",".join("?" for _ in keys)
        return int(jr.execute(f"SELECT COALESCE(SUM(deleted),0) FROM purge_flags WHERE key IN ({marks})",
                              tuple(keys)).fetchone()[0])

    @staticmethod
    def _compact(db: sqlite3.Connection) -> None:
        """Rewrite the file so freed pages (possibly written with secure_delete off) are gone."""
        db.execute("VACUUM")
        busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        if busy:
            raise sqlite3.OperationalError("WAL checkpoint blocked by an open reader; retry the step")

    def delete_owner(self, owner: str) -> dict[str, int]:
        if not owner:
            raise ValueError("owner is required")
        okey = hashlib.sha256(f"owner\0{owner}".encode()).hexdigest()
        counts = {"device_pairings": 0, "devices": 0, "device_commands": 0, "native_commands": 0}
        reg = self._open(self.REGISTRY)
        keys: list[str] = []
        if reg is not None:
            try:
                reg.execute(self._PROGRESS)
                reg.execute("BEGIN IMMEDIATE")
                try:
                    stamp = datetime.now(timezone.utc).isoformat()
                    ids = [r["device_id"] for r in self._select(reg, "SELECT device_id FROM devices WHERE owner_id=?", (owner,))]
                    reg.execute("UPDATE devices SET revoked_at=? WHERE owner_id=? AND revoked_at IS NULL", (stamp, owner))
                    row = reg.execute("SELECT keys FROM purge_progress WHERE owner_key=?", (okey,)).fetchone()
                    keys = sorted(set(json.loads(row["keys"]) if row else []) | {self.purge_key(owner, d) for d in ids})
                    reg.execute("INSERT INTO purge_progress(owner_key,keys) VALUES(?,?) "
                                "ON CONFLICT(owner_key) DO UPDATE SET keys=excluded.keys", (okey, json.dumps(keys)))
                    reg.execute("COMMIT")
                except Exception:
                    reg.execute("ROLLBACK")
                    raise
            except Exception:
                reg.close()
                raise
        jr = self._open(self.JOURNAL)
        touched: set[str] = set()
        try:
            if jr is not None:
                touched |= self._sweep_journal(jr, owner, keys)
            if reg is not None:
                reg.execute("BEGIN IMMEDIATE")
                try:
                    c = reg.execute(
                        "DELETE FROM device_commands WHERE device_id IN (SELECT device_id FROM devices WHERE owner_id=?)",
                        (owner,)).rowcount
                    p = reg.execute("DELETE FROM device_pairings WHERE owner_id=?", (owner,)).rowcount
                    d = reg.execute("DELETE FROM devices WHERE owner_id=?", (owner,)).rowcount
                    reg.execute("UPDATE purge_progress SET pairings=pairings+?,devices=devices+?,commands=commands+? "
                                "WHERE owner_key=?", (p, d, c, okey))
                    reg.execute("COMMIT")
                except Exception:
                    reg.execute("ROLLBACK")
                    raise
            if jr is not None:
                touched |= self._sweep_journal(jr, owner, keys)  # second sweep: late journal writes
                counts["native_commands"] = self._journal_deleted(jr, touched)
                self._compact(jr)
            if reg is not None:
                row = reg.execute("SELECT * FROM purge_progress WHERE owner_key=?", (okey,)).fetchone()
                counts.update(device_pairings=row["pairings"], devices=row["devices"], device_commands=row["commands"])
                self._compact(reg)
        finally:
            if jr is not None:
                jr.close()
            if reg is not None:
                reg.close()
        return counts

    def finish_owner(self, owner: str) -> None:
        """Called once the deletion ledger recorded the step: zero the per-purge counts, drop progress."""
        okey = hashlib.sha256(f"owner\0{owner}".encode()).hexdigest()
        reg = self._open(self.REGISTRY)
        keys: list[str] = []
        if reg is not None:
            try:
                rows = self._select(reg, "SELECT keys FROM purge_progress WHERE owner_key=?", (okey,))
                keys = json.loads(rows[0]["keys"]) if rows else []
                if rows:
                    reg.execute("DELETE FROM purge_progress WHERE owner_key=?", (okey,))
                    self._compact(reg)
            finally:
                reg.close()
        jr = self._open(self.JOURNAL)
        if jr is not None:
            try:
                for key in keys:
                    try:
                        jr.execute("UPDATE purge_flags SET deleted=0 WHERE key=?", (key,))
                    except sqlite3.OperationalError as exc:
                        if "no such table" not in str(exc) and "no such column" not in str(exc):
                            raise
            finally:
                jr.close()

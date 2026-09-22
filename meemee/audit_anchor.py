from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import AuditLog


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def create_anchor(log: AuditLog, destination: Path, signing_key: str) -> dict[str, Any]:
    """Write a signed audit head checkpoint to storage outside the audit database."""
    if len(signing_key.encode()) < 32:
        raise ValueError("audit anchor signing key must be at least 32 bytes")
    if destination.exists():
        raise FileExistsError(destination)
    valid, broken = log.verify()
    if not valid:
        raise ValueError(f"audit chain is invalid at sequence {broken}")
    with log.lock:
        row = log.db.execute("SELECT sequence,entry_hash,occurred_at FROM audit_log ORDER BY sequence DESC LIMIT 1").fetchone()
    payload = {
        "format": "meemee-audit-anchor-v1",
        "sequence": int(row["sequence"]) if row else 0,
        "entry_hash": row["entry_hash"] if row else "0" * 64,
        "entry_occurred_at": row["occurred_at"] if row else None,
        "anchored_at": datetime.now(timezone.utc).isoformat(),
    }
    document = {**payload, "signature": hmac.new(signing_key.encode(), _canonical(payload), hashlib.sha256).hexdigest()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def verify_anchor(log: AuditLog, source: Path, signing_key: str) -> dict[str, Any]:
    document = json.loads(source.read_text())
    signature = document.pop("signature", "")
    expected = hmac.new(signing_key.encode(), _canonical(document), hashlib.sha256).hexdigest()
    signature_valid = hmac.compare_digest(signature, expected)
    sequence = document.get("sequence")
    with log.lock:
        row = log.db.execute("SELECT entry_hash FROM audit_log WHERE sequence=?", (sequence,)).fetchone()
        if row is None:
            row = log.db.execute("SELECT entry_hash FROM audit_chain_base WHERE singleton=1 AND sequence=?", (sequence,)).fetchone()
    chain_valid, broken = log.verify()
    head_matches = bool(row and row["entry_hash"] == document.get("entry_hash")) or (sequence == 0 and row is None)
    status = "pass" if signature_valid and head_matches and chain_valid else "fail"
    return {"status": status, "signature_valid": signature_valid, "head_matches": head_matches, "chain_valid": chain_valid, "broken_sequence": broken, "anchor": document}


def prune_to_anchor(log: AuditLog, source: Path, signing_key: str) -> dict[str, Any]:
    """Delete an anchored audit prefix while retaining a verifiable chain base."""
    report = verify_anchor(log, source, signing_key)
    if report["status"] != "pass":
        raise ValueError("audit anchor verification failed")
    sequence = int(report["anchor"]["sequence"])
    if sequence < 1:
        raise ValueError("cannot prune to an empty-chain anchor")
    entry_hash = report["anchor"]["entry_hash"]
    with log.lock, log.db:
        existing = log.db.execute("SELECT sequence FROM audit_chain_base WHERE singleton=1").fetchone()
        if existing and int(existing["sequence"]) > sequence:
            raise ValueError("anchor predates the current chain base")
        deleted = log.db.execute("DELETE FROM audit_log WHERE sequence<=?", (sequence,)).rowcount
        log.db.execute("INSERT INTO audit_chain_base(singleton,sequence,entry_hash) VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET sequence=excluded.sequence,entry_hash=excluded.entry_hash", (sequence, entry_hash))
    valid, broken = log.verify()
    if not valid:
        raise RuntimeError(f"audit chain invalid after prune at {broken}")
    return {"status":"pruned", "through_sequence":sequence, "deleted_entries":deleted, "retained_chain_valid":True}

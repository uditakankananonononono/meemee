"""PostgreSQL operator tools: account export/import, retention, and signed audit anchors.

These mirror ``meemee.account_export``, ``meemee.retention`` and ``meemee.audit_anchor`` for a
deployment whose records live in the shared database, so an operator on any host acts on the one
set of rows every API server and worker uses (not a stale per-host SQLite file).

- Exports use the same ``meemee.account.v1`` envelope and field encodings as the SQLite export
  (ISO-8601 timestamps, JSON columns as JSON text, ``purge_pending`` as 0/1), so an export taken
  on either backend imports into either backend with the same checksum.
- Import runs in one transaction with collision preflight inside it: any collision or error rolls
  back every table together (no file-copy backup dance is needed).
- Retention runs every window in one transaction; the audit chain is never touched by retention.
- Anchors read the head and verify the chain from one REPEATABLE READ snapshot. Pruning takes the
  audit append advisory lock, so no host can append between the prefix delete and the chain-base
  update, and re-checks the anchored head inside that lock before deleting anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from ._db import Database
from .audit import _LOCK, ZERO, AuditLog

FORMAT = "meemee.account.v1"
ANCHOR_FORMAT = "meemee-audit-anchor-v1"
_JOB_COLUMNS = ("id", "goal", "run_at", "status", "attempts", "max_attempts", "result", "error",
                "created_at", "updated_at", "principal", "purge_pending")
_RUN_COLUMNS = ("run_id", "principal", "goal", "final", "steps_used", "tool_results", "created_at",
                "approvals_required")


def _iso(value: Any) -> Any:
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


def _json_text(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _moment(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ---------------------------------------------------------------- account export / import

def export_account(db: Database, principal: str, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(destination)
    with db.transaction(isolation="REPEATABLE READ") as c:
        job_rows = c.execute(f"SELECT {','.join(_JOB_COLUMNS)} FROM meemee_jobs WHERE principal=%s ORDER BY created_at,id",
                             (principal,)).fetchall()
        run_rows = c.execute(f"SELECT {','.join(_RUN_COLUMNS)} FROM meemee_runs WHERE principal=%s ORDER BY created_at,run_id",
                             (principal,)).fetchall()
        plan_rows = c.execute("SELECT principal,plan,updated_at FROM meemee_principal_plans WHERE principal=%s",
                              (principal,)).fetchall()
    jobs = [{**row, "id": str(row["id"]), "run_at": _iso(row["run_at"]), "created_at": _iso(row["created_at"]),
             "updated_at": _iso(row["updated_at"]), "result": _json_text(row["result"]),
             "purge_pending": int(bool(row["purge_pending"]))} for row in job_rows]
    runs = [{**row, "created_at": _iso(row["created_at"]), "tool_results": json.dumps(row["tool_results"]),
             "approvals_required": json.dumps(row["approvals_required"])} for row in run_rows]
    entitlements = [{**row, "updated_at": _iso(row["updated_at"])} for row in plan_rows]
    payload = {"format": FORMAT, "principal": principal, "exported_at": datetime.now(timezone.utc).isoformat(),
               "jobs": jobs, "runs": runs, "entitlements": entitlements}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    envelope = {"payload": payload, "sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return {"principal": principal, "jobs": len(jobs), "runs": len(runs), "entitlements": len(entitlements),
            "sha256": envelope["sha256"]}


def import_account(db: Database, source: Path, target_principal: str | None = None) -> dict[str, Any]:
    """Import a verified export in one transaction; any collision or failure writes nothing."""
    from meemee.account_export import inspect_import

    plan = inspect_import(source, target_principal); payload = plan["payload"]; target = plan["target_principal"]
    with db.transaction() as c:
        job_ids = [row["id"] for row in payload["jobs"]]; run_ids = [row["run_id"] for row in payload["runs"]]
        if job_ids and c.execute("SELECT 1 FROM meemee_jobs WHERE id = ANY(%s::uuid[]) LIMIT 1", (job_ids,)).fetchone():
            raise ValueError("job ID collision")
        if run_ids and c.execute("SELECT 1 FROM meemee_runs WHERE run_id = ANY(%s) LIMIT 1", (run_ids,)).fetchone():
            raise ValueError("run ID collision")
        if payload["entitlements"] and c.execute("SELECT 1 FROM meemee_principal_plans WHERE principal=%s", (target,)).fetchone():
            raise ValueError("target principal already has an entitlement assignment")
        for row in payload["jobs"]:
            result = _json_value(row.get("result"))
            c.execute("""INSERT INTO meemee_jobs(id,goal,run_at,status,attempts,max_attempts,result,error,created_at,updated_at,principal,purge_pending)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (row["id"], row["goal"], _moment(row["run_at"]), row["status"], row.get("attempts", 0),
                       row.get("max_attempts", 3), Jsonb(result) if result is not None else None, row.get("error"),
                       _moment(row["created_at"]), _moment(row["updated_at"]), target, bool(row.get("purge_pending") or 0)))
        for row in payload["runs"]:
            c.execute("""INSERT INTO meemee_runs(run_id,principal,goal,final,steps_used,tool_results,created_at,approvals_required)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (row["run_id"], target, row["goal"], row["final"], row["steps_used"],
                       Jsonb(_json_value(row["tool_results"])), _moment(row["created_at"]),
                       Jsonb(_json_value(row.get("approvals_required") or "[]"))))
        for row in payload["entitlements"]:
            c.execute("INSERT INTO meemee_principal_plans(principal,plan,updated_at) VALUES(%s,%s,%s)",
                      (target, row["plan"], _moment(row["updated_at"])))
    return {key: plan[key] for key in ("source_principal", "target_principal", "jobs", "runs", "entitlements", "sha256")}


# ---------------------------------------------------------------- retention

def run_retention(db: Database, now: datetime | None = None, jobs_days: int = 30, memory_days: int = 90,
                  audit_days: int = 365, runs_days: int = 90):
    from meemee.retention import RetentionReport

    if min(jobs_days, memory_days, audit_days, runs_days) < 1:
        raise ValueError("retention windows must be at least one day")
    now = now or datetime.now(timezone.utc)
    jobs_cutoff = now - timedelta(days=jobs_days); memory_cutoff = now - timedelta(days=memory_days)
    runs_cutoff = now - timedelta(days=runs_days); audit_cutoff = now - timedelta(days=audit_days)
    terminal = "status IN ('done','failed','cancelled') AND updated_at<%s"
    with db.transaction() as c:
        job_events = c.execute(f"DELETE FROM meemee_job_events WHERE job_id IN (SELECT id FROM meemee_jobs WHERE {terminal})",
                               (jobs_cutoff,)).rowcount
        terminal_jobs = c.execute(f"DELETE FROM meemee_jobs WHERE {terminal}", (jobs_cutoff,)).rowcount
        memories = c.execute("DELETE FROM meemee_memories WHERE created_at<%s", (memory_cutoff,)).rowcount
        runs = c.execute("DELETE FROM meemee_runs WHERE created_at<%s", (runs_cutoff,)).rowcount
        # Tamper-evident audit chains are intentionally retained; pruning needs signed anchors.
        delivered = c.execute("""WITH gone AS (DELETE FROM meemee_webhook_deliveries WHERE status='delivered' AND created_at<%s RETURNING id)
                                 , a AS (DELETE FROM meemee_webhook_attempts WHERE delivery_id IN (SELECT id FROM gone))
                                 SELECT count(*) AS n FROM gone""", (jobs_cutoff,)).fetchone()["n"]
        failed = c.execute("""WITH gone AS (DELETE FROM meemee_webhook_deliveries WHERE status='failed' AND created_at<%s RETURNING id)
                              , a AS (DELETE FROM meemee_webhook_attempts WHERE delivery_id IN (SELECT id FROM gone))
                              SELECT count(*) AS n FROM gone""", (audit_cutoff,)).fetchone()["n"]
        idempotency = c.execute("DELETE FROM meemee_idempotency WHERE expires_at<=%s", (now,)).rowcount
        rate_limits = 0
        if c.execute("SELECT to_regclass('meemee_rate_limits') IS NOT NULL AS present").fetchone()["present"]:
            rate_limits = c.execute("DELETE FROM meemee_rate_limits WHERE window_start<%s",
                                    (int(now.timestamp()) - 86400,)).rowcount
    return RetentionReport(int(job_events), int(terminal_jobs), int(memories), int(runs), 0, int(idempotency),
                           int(rate_limits), int(delivered), int(failed))


# ---------------------------------------------------------------- signed audit anchors

def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _verify_snapshot(base, rows) -> tuple[bool, int | None]:
    previous = base["entry_hash"] if base else ZERO
    floor = int(base["sequence"]) if base else 0
    for row in rows:
        if row["sequence"] <= floor:
            return False, int(row["sequence"])
        digest = AuditLog.hash(previous, _iso(row["occurred_at"]), row["actor_id"], row["action"], row["resource"],
                               row["outcome"], AuditLog._encode(row["metadata"]))
        if row["previous_hash"] != previous or row["entry_hash"] != digest:
            return False, int(row["sequence"])
        previous = row["entry_hash"]
    return True, None


def _snapshot(c):
    base = c.execute("SELECT sequence,entry_hash FROM meemee_audit_chain_base WHERE singleton=1").fetchone()
    rows = c.execute("SELECT * FROM meemee_audit_log ORDER BY sequence").fetchall()
    return base, rows


def create_anchor(db: Database, destination: Path, signing_key: str) -> dict[str, Any]:
    """Sign the head of the shared chain; the head and the verification come from one snapshot."""
    if len(signing_key.encode()) < 32:
        raise ValueError("audit anchor signing key must be at least 32 bytes")
    if destination.exists():
        raise FileExistsError(destination)
    with db.transaction(isolation="REPEATABLE READ") as c:
        base, rows = _snapshot(c)
    valid, broken = _verify_snapshot(base, rows)
    if not valid:
        raise ValueError(f"audit chain is invalid at sequence {broken}")
    head = rows[-1] if rows else None
    if head is not None:
        sequence, entry_hash, occurred = int(head["sequence"]), head["entry_hash"], _iso(head["occurred_at"])
    elif base is not None:  # fully pruned chain: the base is the head
        sequence, entry_hash, occurred = int(base["sequence"]), base["entry_hash"], None
    else:
        sequence, entry_hash, occurred = 0, ZERO, None
    payload = {"format": ANCHOR_FORMAT, "sequence": sequence, "entry_hash": entry_hash,
               "entry_occurred_at": occurred, "anchored_at": datetime.now(timezone.utc).isoformat()}
    document = {**payload, "signature": hmac.new(signing_key.encode(), _canonical(payload), hashlib.sha256).hexdigest()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def _head_matches(c, sequence: Any, entry_hash: Any) -> bool:
    row = c.execute("SELECT entry_hash FROM meemee_audit_log WHERE sequence=%s", (sequence,)).fetchone()
    if row is None:
        row = c.execute("SELECT entry_hash FROM meemee_audit_chain_base WHERE singleton=1 AND sequence=%s", (sequence,)).fetchone()
    return bool(row and row["entry_hash"] == entry_hash) or (sequence == 0 and row is None)


def verify_anchor(db: Database, source: Path, signing_key: str) -> dict[str, Any]:
    document = json.loads(source.read_text())
    signature = document.pop("signature", "")
    expected = hmac.new(signing_key.encode(), _canonical(document), hashlib.sha256).hexdigest()
    signature_valid = hmac.compare_digest(signature, expected)
    sequence = document.get("sequence")
    if not isinstance(sequence, int):
        return {"status": "fail", "signature_valid": signature_valid, "head_matches": False, "chain_valid": False,
                "broken_sequence": None, "anchor": document}
    with db.transaction(isolation="REPEATABLE READ") as c:
        head_matches = _head_matches(c, sequence, document.get("entry_hash"))
        base, rows = _snapshot(c)
    chain_valid, broken = _verify_snapshot(base, rows)
    status = "pass" if signature_valid and head_matches and chain_valid else "fail"
    return {"status": status, "signature_valid": signature_valid, "head_matches": head_matches,
            "chain_valid": chain_valid, "broken_sequence": broken, "anchor": document}


def prune_to_anchor(db: Database, source: Path, signing_key: str) -> dict[str, Any]:
    """Delete an anchored prefix of the shared chain and record the chain base, atomically."""
    report = verify_anchor(db, source, signing_key)
    if report["status"] != "pass":
        raise ValueError("audit anchor verification failed")
    sequence = int(report["anchor"]["sequence"])
    if sequence < 1:
        raise ValueError("cannot prune to an empty-chain anchor")
    entry_hash = report["anchor"]["entry_hash"]
    with db.transaction(isolation="READ COMMITTED") as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK,))  # same lock every append takes
        existing = c.execute("SELECT sequence FROM meemee_audit_chain_base WHERE singleton=1").fetchone()
        if existing and int(existing["sequence"]) > sequence:
            raise ValueError("anchor predates the current chain base")
        if not _head_matches(c, sequence, entry_hash):
            raise ValueError("anchored entry changed before prune")
        deleted = c.execute("DELETE FROM meemee_audit_log WHERE sequence<=%s", (sequence,)).rowcount
        c.execute("""INSERT INTO meemee_audit_chain_base(singleton,sequence,entry_hash) VALUES(1,%s,%s)
                     ON CONFLICT(singleton) DO UPDATE SET sequence=excluded.sequence,entry_hash=excluded.entry_hash""",
                  (sequence, entry_hash))
        base, rows = _snapshot(c)
        valid, broken = _verify_snapshot(base, rows)
        if not valid:  # raising rolls back the delete and the base update together
            raise RuntimeError(f"audit chain invalid after prune at {broken}")
    return {"status": "pruned", "through_sequence": sequence, "deleted_entries": int(deleted), "retained_chain_valid": True}

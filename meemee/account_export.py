from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ._sqlite_guard import rollback_quietly

FORMAT = "meemee.account.v1"


def _rows(path: Path, query: str, parameters: tuple) -> list[dict]:
    if not path.exists(): return []
    db=sqlite3.connect(path); db.row_factory=sqlite3.Row
    try: return [dict(row) for row in db.execute(query,parameters).fetchall()]
    finally: db.close()


def export_account(data_dir: Path, principal: str, destination: Path) -> dict:
    if destination.exists(): raise FileExistsError(destination)
    jobs=_rows(data_dir/"jobs.sqlite3","SELECT * FROM jobs WHERE principal=? ORDER BY created_at,id",(principal,))
    runs=_rows(data_dir/"runs.sqlite3","SELECT * FROM runs WHERE principal=? ORDER BY created_at,run_id",(principal,))
    entitlements=_rows(data_dir/"entitlements.sqlite3","SELECT principal,plan,updated_at FROM principal_plans WHERE principal=?",(principal,))
    payload={"format":FORMAT,"principal":principal,"exported_at":datetime.now(timezone.utc).isoformat(),"jobs":jobs,"runs":runs,"entitlements":entitlements}
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":"))
    envelope={"payload":payload,"sha256":hashlib.sha256(canonical.encode()).hexdigest()}
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".meemee-export-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(envelope,indent=2,sort_keys=True)+"\n")
            output.flush()
            os.fsync(output.fileno())
        # Same-directory hard-link publication is atomic and refuses overwrite.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"principal":principal,"jobs":len(jobs),"runs":len(runs),"entitlements":len(entitlements),"sha256":envelope["sha256"]}


def inspect_import(source: Path, target_principal: str | None = None) -> dict:
    envelope=json.loads(source.read_text()); payload=envelope["payload"]
    if payload.get("format")!=FORMAT: raise ValueError("unsupported account export format")
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":"))
    digest=hashlib.sha256(canonical.encode()).hexdigest()
    if digest!=envelope.get("sha256"): raise ValueError("account export checksum mismatch")
    source_principal=payload["principal"]; target=target_principal or source_principal
    return {"source_principal":source_principal,"target_principal":target,"jobs":len(payload["jobs"]),"runs":len(payload["runs"]),"entitlements":len(payload["entitlements"]),"sha256":digest,"payload":payload}


def import_account(data_dir: Path, source: Path, target_principal: str | None = None) -> dict:
    """Import with one attached-database transaction, never old-file replacement."""
    plan = inspect_import(source, target_principal)
    payload, target = plan["payload"], plan["target_principal"]
    paths = {name: data_dir / f"{name}.sqlite3" for name in ("jobs", "runs", "entitlements")}
    for path in paths.values():
        if not path.exists():
            raise FileNotFoundError(f"target database missing: {path.name}")
    db = sqlite3.connect(paths["jobs"])
    try:
        db.execute("ATTACH DATABASE ? AS imported_runs", (str(paths["runs"]),))
        db.execute("ATTACH DATABASE ? AS imported_entitlements", (str(paths["entitlements"]),))
        db.execute("BEGIN IMMEDIATE")
        if any(db.execute("SELECT 1 FROM jobs WHERE id=?", (row["id"],)).fetchone() for row in payload["jobs"]):
            raise ValueError("job ID collision")
        if any(db.execute("SELECT 1 FROM imported_runs.runs WHERE run_id=?", (row["run_id"],)).fetchone() for row in payload["runs"]):
            raise ValueError("run ID collision")
        if payload["entitlements"] and db.execute("SELECT 1 FROM imported_entitlements.principal_plans WHERE principal=?", (target,)).fetchone():
            raise ValueError("target principal already has an entitlement assignment")
        for kind, table, schema in (("jobs", "jobs", "main"), ("runs", "runs", "imported_runs")):
            allowed = {row[1] for row in db.execute(f"PRAGMA {schema}.table_info({table})")}
            for row in payload[kind]:
                values = dict(row)
                values["principal"] = target
                if not set(values) <= allowed:
                    raise ValueError(f"unsupported {kind} columns")
                columns = list(values)
                quoted = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
                db.execute(f"INSERT INTO {schema}.{table}({quoted}) VALUES({','.join('?' for _ in columns)})", tuple(values[c] for c in columns))
        for row in payload["entitlements"]:
            db.execute("INSERT INTO imported_entitlements.principal_plans(principal,plan,updated_at) VALUES(?,?,?)", (target, row["plan"], row["updated_at"]))
        db.commit()
    except BaseException:
        rollback_quietly(db)
        raise
    finally:
        db.close()
    return {key: plan[key] for key in ("source_principal", "target_principal", "jobs", "runs", "entitlements", "sha256")}

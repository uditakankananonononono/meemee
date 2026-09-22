from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    destination.write_text(json.dumps(envelope,indent=2,sort_keys=True)+"\n")
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
    """Import a verified export with collision preflight and rollback-safe database copies."""
    import shutil
    import tempfile

    plan=inspect_import(source,target_principal); payload=plan["payload"]; target=plan["target_principal"]
    paths={"jobs":data_dir/"jobs.sqlite3","runs":data_dir/"runs.sqlite3","entitlements":data_dir/"entitlements.sqlite3"}
    for path in paths.values():
        if not path.exists(): raise FileNotFoundError(f"target database missing: {path.name}")
    jobs=sqlite3.connect(paths["jobs"]); runs=sqlite3.connect(paths["runs"]); entitlements=sqlite3.connect(paths["entitlements"])
    try:
        job_ids=[row["id"] for row in payload["jobs"]]; run_ids=[row["run_id"] for row in payload["runs"]]
        if any(jobs.execute("SELECT 1 FROM jobs WHERE id=?",(ident,)).fetchone() for ident in job_ids): raise ValueError("job ID collision")
        if any(runs.execute("SELECT 1 FROM runs WHERE run_id=?",(ident,)).fetchone() for ident in run_ids): raise ValueError("run ID collision")
        if payload["entitlements"] and entitlements.execute("SELECT 1 FROM principal_plans WHERE principal=?",(target,)).fetchone(): raise ValueError("target principal already has an entitlement assignment")
    finally:
        jobs.close(); runs.close(); entitlements.close()
    with tempfile.TemporaryDirectory(prefix="meemee-account-import-",dir=data_dir) as temporary:
        backup_dir=Path(temporary); backups={path:backup_dir/path.name for path in paths.values()}
        for path,backup in backups.items(): shutil.copy2(path,backup)
        jobs=sqlite3.connect(paths["jobs"]); runs=sqlite3.connect(paths["runs"]); entitlements=sqlite3.connect(paths["entitlements"])
        try:
            jobs.execute("BEGIN IMMEDIATE"); runs.execute("BEGIN IMMEDIATE"); entitlements.execute("BEGIN IMMEDIATE")
            for row in payload["jobs"]:
                values=dict(row); values["principal"]=target
                columns=list(values); jobs.execute(f"INSERT INTO jobs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",tuple(values[c] for c in columns))
            for row in payload["runs"]:
                values=dict(row); values["principal"]=target
                columns=list(values); runs.execute(f"INSERT INTO runs({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",tuple(values[c] for c in columns))
            for row in payload["entitlements"]:
                entitlements.execute("INSERT INTO principal_plans(principal,plan,updated_at) VALUES(?,?,?)",(target,row["plan"],row["updated_at"]))
            entitlements.commit(); runs.commit(); jobs.commit()
        except Exception:
            jobs.rollback(); runs.rollback(); entitlements.rollback(); jobs.close(); runs.close(); entitlements.close()
            for path,backup in backups.items(): shutil.copy2(backup,path)
            raise
        finally:
            jobs.close()
            runs.close()
            entitlements.close()
    return {key:plan[key] for key in ("source_principal","target_principal","jobs","runs","entitlements","sha256")}

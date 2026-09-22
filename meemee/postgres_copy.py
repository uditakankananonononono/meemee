from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

TABLES = {
    "memories": ("meemee.sqlite3", "memories", "id"),
    "jobs": ("jobs.sqlite3", "jobs", "id"),
    "job_events": ("jobs.sqlite3", "job_events", "sequence"),
    "api_tokens": ("auth.sqlite3", "api_tokens", "id"),
    "audit_log": ("audit.sqlite3", "audit_log", "sequence"),
}


def export_sqlite(data_dir: Path, destination: Path) -> dict[str, Any]:
    if destination.exists(): raise FileExistsError(destination)
    tables: dict[str, list[dict[str, Any]]] = {}
    for name,(filename,source,key) in TABLES.items():
        path=data_dir/filename
        if not path.exists(): tables[name]=[]; continue
        db=sqlite3.connect(path); db.row_factory=sqlite3.Row
        try:
            available=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(source,)).fetchone()
            rows=[dict(row) for row in db.execute(f"SELECT * FROM {source} ORDER BY {key}")] if available else []
            for row in rows:
                for field,value in tuple(row.items()):
                    if isinstance(value,bytes): row[field]={"$hex":value.hex()}
            tables[name]=rows
        finally: db.close()
    payload={"format":"meemee-sqlite-postgresql-v1","tables":tables}
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"))
    document={**payload,"sha256":hashlib.sha256(encoded.encode()).hexdigest()}
    destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps(document,indent=2,sort_keys=True)+"\n")
    return {"status":"exported","counts":{k:len(v) for k,v in tables.items()},"sha256":document["sha256"]}


def verify_export(source: Path) -> dict[str, Any]:
    document=json.loads(source.read_text()); supplied=document.pop("sha256","")
    expected=hashlib.sha256(json.dumps(document,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if document.get("format")!="meemee-sqlite-postgresql-v1" or supplied!=expected: raise ValueError("copy export checksum or format is invalid")
    return document


def import_postgresql(source: Path, database, *, require_empty: bool = True) -> dict[str, Any]:
    document=verify_export(source); counts={}
    with database.transaction(isolation="SERIALIZABLE") as connection:
        for table,rows in document["tables"].items():
            target=f"meemee_{table}"
            if require_empty and connection.execute(f"SELECT 1 FROM {target} LIMIT 1").fetchone(): raise ValueError(f"target table is not empty: {target}")
            for row in rows:
                restored={key:(bytes.fromhex(value["$hex"]) if isinstance(value,dict) and "$hex" in value else value) for key,value in row.items()}
                columns=list(restored); placeholders=",".join(["%s"]*len(columns))
                connection.execute(f"INSERT INTO {target}({','.join(columns)}) VALUES({placeholders})",tuple(restored[column] for column in columns))
            counts[table]=len(rows)
    return {"status":"imported","counts":counts}

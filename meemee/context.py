from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Kind = Literal["event", "document", "entity", "relationship"]
Visibility = Literal["private", "agent", "shared"]


@dataclass(frozen=True)
class ContextRecord:
    owner_id: str
    source_id: str
    external_id: str
    kind: Kind
    title: str
    content: str
    occurred_at: str
    provenance: dict[str, Any]
    visibility: Visibility = "private"
    cursor: str | None = None
    metadata: dict[str, Any] | None = None


class ContextStore:
    """Owner-scoped source/event ledger and unified context index."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS context_sources(
                  owner_id TEXT NOT NULL, source_id TEXT NOT NULL, connector TEXT NOT NULL,
                  config TEXT NOT NULL, permission TEXT NOT NULL, cursor TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  PRIMARY KEY(owner_id,source_id));
                CREATE TABLE IF NOT EXISTS context_records(
                  id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, source_id TEXT NOT NULL,
                  external_id TEXT NOT NULL, content_hash TEXT NOT NULL, kind TEXT NOT NULL,
                  title TEXT NOT NULL, content TEXT NOT NULL, occurred_at TEXT NOT NULL,
                  provenance TEXT NOT NULL, visibility TEXT NOT NULL, cursor TEXT, metadata TEXT NOT NULL,
                  ingested_at TEXT NOT NULL, UNIQUE(owner_id,source_id,external_id,content_hash));
                CREATE INDEX IF NOT EXISTS context_owner_time ON context_records(owner_id,occurred_at DESC,id DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(title,content,content='context_records',content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS context_ai AFTER INSERT ON context_records BEGIN
                  INSERT INTO context_fts(rowid,title,content) VALUES(new.id,new.title,new.content);
                END;
            """)

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() is not None

    def register_source(self, owner_id: str, source_id: str, connector: str, config: dict, permission: Visibility = "private") -> dict:
        if not owner_id or not source_id or not connector: raise ValueError("owner, source and connector are required")
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self.db:
            self.db.execute("INSERT INTO context_sources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,source_id) DO UPDATE SET connector=excluded.connector,config=excluded.config,permission=excluded.permission,updated_at=excluded.updated_at",(owner_id,source_id,connector,json.dumps(config,sort_keys=True),permission,None,now,now))
        return self.source(owner_id,source_id) or {}

    def source(self, owner_id: str, source_id: str) -> dict | None:
        with self.lock: row=self.db.execute("SELECT * FROM context_sources WHERE owner_id=? AND source_id=?",(owner_id,source_id)).fetchone()
        if not row:return None
        result=dict(row);result["config"]=json.loads(result["config"]);return result

    def cursor(self, owner_id: str, source_id: str) -> str | None:
        row=self.source(owner_id,source_id);return row["cursor"] if row else None

    def ingest(self, record: ContextRecord) -> bool:
        if self.source(record.owner_id,record.source_id) is None: raise ValueError("source is not registered for owner")
        if record.visibility not in {"private","agent","shared"}: raise ValueError("invalid visibility")
        digest=hashlib.sha256((record.title+"\0"+record.content).encode()).hexdigest();now=datetime.now(timezone.utc).isoformat()
        with self.lock,self.db:
            inserted=self.db.execute("INSERT OR IGNORE INTO context_records(owner_id,source_id,external_id,content_hash,kind,title,content,occurred_at,provenance,visibility,cursor,metadata,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(record.owner_id,record.source_id,record.external_id,digest,record.kind,record.title,record.content,record.occurred_at,json.dumps(record.provenance,sort_keys=True),record.visibility,record.cursor,json.dumps(record.metadata or {},sort_keys=True),now)).rowcount
            if record.cursor is not None:self.db.execute("UPDATE context_sources SET cursor=?,updated_at=? WHERE owner_id=? AND source_id=?",(record.cursor,now,record.owner_id,record.source_id))
        return bool(inserted)

    def search(self, owner_id: str, query: str, limit: int = 12, allowed: set[str] | None = None) -> list[dict]:
        visibility=allowed or {"private","agent"};placeholders=','.join('?' for _ in visibility)
        sql=f"""SELECT r.*,bm25(context_fts) score FROM context_fts JOIN context_records r ON r.id=context_fts.rowid
        WHERE context_fts MATCH ? AND r.owner_id=? AND r.visibility IN ({placeholders}) ORDER BY score,r.occurred_at DESC LIMIT ?"""
        with self.lock: rows=self.db.execute(sql,(query,owner_id,*sorted(visibility),max(1,min(limit,100)))).fetchall()
        return [self._record(row) for row in rows]

    def owner_watermarks(self) -> dict[str, int]:
        """Highest context record id per owner; lets schedulers skip owners with nothing new."""
        with self.lock:
            rows = self.db.execute("SELECT owner_id, MAX(id) AS top FROM context_records GROUP BY owner_id").fetchall()
        return {row["owner_id"]: int(row["top"]) for row in rows}

    def recent(self, owner_id: str, limit: int = 12, allowed: set[str] | None = None) -> list[dict]:
        visibility=allowed or {"private","agent"};placeholders=','.join('?' for _ in visibility)
        with self.lock:rows=self.db.execute(f"SELECT * FROM context_records WHERE owner_id=? AND visibility IN ({placeholders}) ORDER BY occurred_at DESC,id DESC LIMIT ?",(owner_id,*sorted(visibility),max(1,min(limit,100)))).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row) -> dict:
        result=dict(row);result["provenance"]=json.loads(result["provenance"]);result["metadata"]=json.loads(result["metadata"]);return result

    def assemble(self, owner_id: str, query: str, limit: int = 12) -> dict:
        try:records=self.search(owner_id,query,limit)
        except sqlite3.OperationalError:records=[]
        if len(records)<limit:
            seen={row["id"] for row in records}
            records += [row for row in self.recent(owner_id,limit*2) if row["id"] not in seen][:limit-len(records)]
        return {"owner_id":owner_id,"query":query,"records":records,"sources":sorted({row["source_id"] for row in records})}

    def purge_owner(self, owner_id: str) -> dict[str, int]:
        """Hard-delete an owner's connected sources and ingested records, including the FTS index."""
        if not owner_id:
            raise ValueError("owner is required")
        with self.lock, self.db:
            rows = self.db.execute("SELECT id,title,content FROM context_records WHERE owner_id=?", (owner_id,)).fetchall()
            for row in rows:
                self.db.execute(
                    "INSERT INTO context_fts(context_fts,rowid,title,content) VALUES('delete',?,?,?)",
                    (row["id"], row["title"], row["content"]),
                )
            records = self.db.execute("DELETE FROM context_records WHERE owner_id=?", (owner_id,)).rowcount
            sources = self.db.execute("DELETE FROM context_sources WHERE owner_id=?", (owner_id,)).rowcount
        return {"context_records": records, "context_sources": sources}


def verify_webhook_signature(secret: str, body: bytes, timestamp: str, signature: str, now: int | None = None, tolerance: int = 300) -> None:
    clock=int(datetime.now(timezone.utc).timestamp()) if now is None else now
    try:stamp=int(timestamp)
    except ValueError as exc:raise ValueError("invalid webhook timestamp") from exc
    if abs(clock-stamp)>tolerance:raise ValueError("stale webhook timestamp")
    expected=hmac.new(secret.encode(),timestamp.encode()+b"."+body,hashlib.sha256).hexdigest()
    supplied=signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected,supplied):raise ValueError("invalid webhook signature")

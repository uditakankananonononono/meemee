from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("webhook URL must use HTTPS")
    for address in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("webhook URL resolves to a private or reserved address")
    return url


class WebhookStore:
    """Durable webhook subscriptions and transactional delivery outbox."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, url TEXT NOT NULL,
                secret TEXT NOT NULL, events TEXT NOT NULL, active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_id TEXT NOT NULL,
                event_type TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL,
                response_status INTEGER, last_error TEXT, created_at TEXT NOT NULL,
                UNIQUE(subscription_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS webhook_due ON webhook_deliveries(status,next_attempt_at);
        """)

    def subscribe(self, principal: str, url: str, events: set[str]) -> tuple[str, str]:
        validate_webhook_url(url)
        if not events or any(not event.strip() for event in events):
            raise ValueError("at least one non-empty event is required")
        ident, secret = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO webhook_subscriptions(id,principal,url,secret,events,active,created_at) VALUES(?,?,?,?,?,1,?)",
                (ident, principal, url, secret, " ".join(sorted(events)), datetime.now(timezone.utc).isoformat()),
            )
        return ident, secret

    def unsubscribe(self, ident: str, principal: str) -> bool:
        with self.lock, self.db:
            return bool(self.db.execute(
                "UPDATE webhook_subscriptions SET active=0 WHERE id=? AND principal=? AND active=1",
                (ident, principal),
            ).rowcount)

    def enqueue(self, event_id: str, event_type: str, payload: dict) -> int:
        encoded, now = json.dumps(payload, sort_keys=True, separators=(",", ":")), time.time()
        with self.lock, self.db:
            subscriptions = self.db.execute("SELECT id,events FROM webhook_subscriptions WHERE active=1").fetchall()
            created = 0
            for subscription in subscriptions:
                if event_type not in subscription["events"].split() and "*" not in subscription["events"].split():
                    continue
                created += self.db.execute(
                    "INSERT OR IGNORE INTO webhook_deliveries(id,subscription_id,event_id,event_type,payload,status,next_attempt_at,created_at) VALUES(?,?,?,?,?,'queued',?,?)",
                    (uuid.uuid4().hex, subscription["id"], event_id, event_type, encoded, now, datetime.now(timezone.utc).isoformat()),
                ).rowcount
        return created

    def claim(self, now: float | None = None) -> dict | None:
        current = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("""SELECT d.*,s.url,s.secret FROM webhook_deliveries d
                JOIN webhook_subscriptions s ON s.id=d.subscription_id
                WHERE d.status='queued' AND d.next_attempt_at<=? AND s.active=1
                ORDER BY d.next_attempt_at,d.id LIMIT 1""", (current,)).fetchone()
            if row is None:
                self.db.execute("COMMIT"); return None
            self.db.execute("UPDATE webhook_deliveries SET status='sending',attempts=attempts+1 WHERE id=?", (row["id"],))
            self.db.execute("COMMIT")
        return dict(row)

    def succeed(self, ident: str, status: int) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE webhook_deliveries SET status='delivered',response_status=?,last_error=NULL WHERE id=? AND status='sending'", (status, ident))

    def retry(self, ident: str, error: str, max_attempts: int = 8) -> None:
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM webhook_deliveries WHERE id=?", (ident,)).fetchone()
            if row is None: return
            terminal = row["attempts"] >= max_attempts
            delay = min(2 ** max(row["attempts"] - 1, 0), 3600)
            self.db.execute("UPDATE webhook_deliveries SET status=?,next_attempt_at=?,last_error=? WHERE id=?", ("failed" if terminal else "queued", time.time()+delay, error[:1000], ident))


class WebhookDispatcher:
    def __init__(self, store: WebhookStore, client: httpx.AsyncClient | None = None):
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False)

    @staticmethod
    def signature(secret: str, timestamp: str, body: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()

    async def deliver_one(self) -> bool:
        delivery = self.store.claim()
        if delivery is None: return False
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type":"application/json",
            "X-Meemee-Event":delivery["event_type"],
            "X-Meemee-Delivery":delivery["id"],
            "X-Meemee-Timestamp":timestamp,
            "X-Meemee-Signature-256":self.signature(delivery["secret"], timestamp, delivery["payload"]),
        }
        try:
            response = await self.client.post(delivery["url"], content=delivery["payload"], headers=headers)
            if 200 <= response.status_code < 300:
                self.store.succeed(delivery["id"], response.status_code)
            else:
                self.store.retry(delivery["id"], f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            self.store.retry(delivery["id"], type(exc).__name__)
        return True

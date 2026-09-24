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

from .cursors import decode_cursor, encode_cursor
from .schema_registry import register_schema
from .secret_cipher import SecretCipher

PRIVATE_HOSTS_ENV = "MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS"
RESERVED_HEADERS = frozenset({"authorization", "cookie", "host", "content-length", "content-type", "x-meemee-signature-256",
                              "x-meemee-timestamp", "x-meemee-delivery", "x-meemee-event"})


def private_hosts_allowed() -> bool:
    """Test-only SSRF override: MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS=1 lets webhook URLs resolve to
    loopback/private addresses so a local receiver can be exercised end to end. HTTPS is still
    required. NEVER set it in production; with MEEMEE_ENV=production it raises instead of applying."""
    from .config import Settings

    settings = Settings()
    if not settings.webhook_allow_private_hosts:
        return False
    if settings.env.strip().lower() == "production":
        raise RuntimeError(f"{PRIVATE_HOSTS_ENV} is test-only and is refused when MEEMEE_ENV=production")
    return True


def check_private_hosts_override(component: str) -> None:
    """Startup guard: refuse to run with the test-only override in production, warn loudly otherwise."""
    if private_hosts_allowed():
        import logging

        logging.getLogger("meemee.webhooks").warning(
            "%s: %s=1 - webhook SSRF protection is OFF (test-only; never use on a deployed instance)",
            component, PRIVATE_HOSTS_ENV)


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("webhook URL must use HTTPS")
    if private_hosts_allowed():
        return url
    for address in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("webhook URL resolves to a private or reserved address")
    return url


class WebhookStore:
    """Durable webhook subscriptions and transactional delivery outbox."""

    def __init__(self, path: Path, max_payload_bytes: int = 256_000, encryption_key: str | None = None):
        if max_payload_bytes < 1024:
            raise ValueError("webhook payload ceiling must be at least 1024 bytes")
        self.max_payload_bytes = max_payload_bytes
        if not encryption_key:
            raise ValueError("webhook secret encryption requires MEEMEE_VAULT_KEY")
        self.secret_cipher = SecretCipher(encryption_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, url TEXT NOT NULL,
                secret TEXT NOT NULL, events TEXT NOT NULL, fields TEXT, headers TEXT, active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_id TEXT NOT NULL,
                event_type TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL,
                response_status INTEGER, last_error TEXT, sending_started_at REAL, created_at TEXT NOT NULL,
                UNIQUE(subscription_id,event_id)
            );
            CREATE INDEX IF NOT EXISTS webhook_due ON webhook_deliveries(status,next_attempt_at);
            CREATE TABLE IF NOT EXISTS webhook_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT NOT NULL,
                attempt INTEGER NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                outcome TEXT, response_status INTEGER, error TEXT
            );
            CREATE INDEX IF NOT EXISTS webhook_attempt_delivery ON webhook_attempts(delivery_id,id);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(webhook_deliveries)")}
        if "payload_sha256" not in columns:
            self.db.execute("ALTER TABLE webhook_deliveries ADD COLUMN payload_sha256 TEXT")
        subscription_columns = {row[1] for row in self.db.execute("PRAGMA table_info(webhook_subscriptions)")}
        if "fields" not in subscription_columns:
            self.db.execute("ALTER TABLE webhook_subscriptions ADD COLUMN fields TEXT")
        if "headers" not in subscription_columns:
            self.db.execute("ALTER TABLE webhook_subscriptions ADD COLUMN headers TEXT")
        if "sending_started_at" not in columns:
            self.db.execute("ALTER TABLE webhook_deliveries ADD COLUMN sending_started_at REAL")
        register_schema(self.db, "webhooks", 4, [
            "subscriptions encrypted secret events fields headers active",
            "deliveries payload hash sending lease",
            "attempt history and due indexes",
        ])
        self._encrypt_legacy_secrets()

    def _secret_context(self, ident: str) -> str:
        return f"webhook-subscription:{ident}"

    def _encrypt_legacy_secrets(self) -> None:
        """Upgrade legacy plaintext secrets in one transaction; safe to resume."""
        with self.lock, self.db:
            rows = self.db.execute("SELECT id,secret FROM webhook_subscriptions").fetchall()
            for row in rows:
                if not row["secret"].startswith(SecretCipher.PREFIX):
                    encrypted = self.secret_cipher.encrypt(
                        row["secret"], self._secret_context(row["id"])
                    )
                    self.db.execute(
                        "UPDATE webhook_subscriptions SET secret=? WHERE id=?",
                        (encrypted, row["id"]),
                    )
                else:
                    # Fail closed at startup if the configured key cannot read stored secrets.
                    self.secret_cipher.decrypt(
                        row["secret"], self._secret_context(row["id"])
                    )

    def _encrypt_secret(self, ident: str, secret: str) -> str:
        return self.secret_cipher.encrypt(secret, self._secret_context(ident))

    def _decrypt_secret(self, ident: str, secret: str) -> str:
        return self.secret_cipher.decrypt(secret, self._secret_context(ident))

    def subscribe(
        self,
        principal: str,
        url: str,
        events: set[str],
        fields: set[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        validate_webhook_url(url)
        if not events or any(not event.strip() for event in events):
            raise ValueError("at least one non-empty event is required")
        reserved = RESERVED_HEADERS
        headers = headers or {}
        if any(name.lower() in reserved or name.lower().startswith("proxy-") for name in headers):
            raise ValueError("custom headers contain a forbidden sensitive or transport name")
        if len(headers) > 20 or any(len(name)>100 or len(value)>1000 for name,value in headers.items()):
            raise ValueError("custom headers exceed count or size limits")
        ident, secret = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO webhook_subscriptions(id,principal,url,secret,events,fields,headers,active,created_at) VALUES(?,?,?,?,?,?,?,1,?)",
                (ident, principal, url, self._encrypt_secret(ident, secret), " ".join(sorted(events)), " ".join(sorted(fields or ())) or None, json.dumps(headers, sort_keys=True) if headers else None, datetime.now(timezone.utc).isoformat()),
            )
        return ident, secret

    def unsubscribe(self, ident: str, principal: str) -> bool:
        with self.lock, self.db:
            return bool(self.db.execute(
                "UPDATE webhook_subscriptions SET active=0 WHERE id=? AND principal=? AND active=1",
                (ident, principal),
            ).rowcount)

    def enqueue(self, event_id: str, event_type: str, payload: dict, *, principal: str) -> int:
        """Queue one delivery per matching subscription owned by `principal` (the event's owner).

        Subscriptions of other principals never receive the event."""
        envelope = {"schema":"meemee.webhook.v1","event_id":event_id,"event_type":event_type,"data":payload}
        encoded, now = json.dumps(envelope, sort_keys=True, separators=(",", ":")), time.time()
        if len(encoded.encode()) > self.max_payload_bytes:
            raise ValueError(f"webhook payload exceeds {self.max_payload_bytes} bytes")
        with self.lock, self.db:
            subscriptions = self.db.execute(
                "SELECT id,events,fields FROM webhook_subscriptions WHERE active=1 AND principal=?", (principal,)
            ).fetchall()
            created = 0
            for subscription in subscriptions:
                if event_type not in subscription["events"].split() and "*" not in subscription["events"].split():
                    continue
                body = encoded
                if subscription["fields"]:
                    selected = {key: payload[key] for key in subscription["fields"].split() if key in payload}
                    selected_envelope = {"schema":"meemee.webhook.v1","event_id":event_id,"event_type":event_type,"data":selected}
                    body = json.dumps(selected_envelope, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(body.encode()).hexdigest()
                created += self.db.execute(
                    "INSERT OR IGNORE INTO webhook_deliveries(id,subscription_id,event_id,event_type,payload,payload_sha256,status,next_attempt_at,created_at) VALUES(?,?,?,?,?,?,'queued',?,?)",
                    (uuid.uuid4().hex, subscription["id"], event_id, event_type, body, digest, now, datetime.now(timezone.utc).isoformat()),
                ).rowcount
        return created

    def claim(self, now: float | None = None) -> dict | None:
        current = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("""SELECT d.*,s.url,s.secret,s.headers FROM webhook_deliveries d
                JOIN webhook_subscriptions s ON s.id=d.subscription_id
                WHERE d.status='queued' AND d.next_attempt_at<=? AND s.active=1
                ORDER BY d.next_attempt_at,d.id LIMIT 1""", (current,)).fetchone()
            if row is None:
                self.db.execute("COMMIT"); return None
            self.db.execute("UPDATE webhook_deliveries SET status='sending',attempts=attempts+1,sending_started_at=? WHERE id=?", (current, row["id"]))
            self.db.execute(
                "INSERT INTO webhook_attempts(delivery_id,attempt,started_at) VALUES(?,?,?)",
                (row["id"], row["attempts"] + 1, datetime.now(timezone.utc).isoformat()),
            )
            self.db.execute("COMMIT")
        delivery = dict(row)
        delivery["secret"] = self._decrypt_secret(delivery["subscription_id"], delivery["secret"])
        return delivery

    def succeed(self, ident: str, status: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute("UPDATE webhook_deliveries SET status='delivered',response_status=?,last_error=NULL,sending_started_at=NULL WHERE id=? AND status='sending'", (status, ident))
            self.db.execute(
                "UPDATE webhook_attempts SET finished_at=?,outcome='delivered',response_status=? WHERE id=(SELECT max(id) FROM webhook_attempts WHERE delivery_id=?)",
                (now, status, ident),
            )

    def retry(self, ident: str, error: str, max_attempts: int = 8) -> None:
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM webhook_deliveries WHERE id=?", (ident,)).fetchone()
            if row is None: return
            terminal = row["attempts"] >= max_attempts
            delay = min(2 ** max(row["attempts"] - 1, 0), 3600)
            status = "failed" if terminal else "queued"
            self.db.execute("UPDATE webhook_deliveries SET status=?,next_attempt_at=?,last_error=?,sending_started_at=NULL WHERE id=?", (status, time.time()+delay, error[:1000], ident))
            self.db.execute(
                "UPDATE webhook_attempts SET finished_at=?,outcome=?,error=? WHERE id=(SELECT max(id) FROM webhook_attempts WHERE delivery_id=?)",
                (datetime.now(timezone.utc).isoformat(), status, error[:1000], ident),
            )
            if terminal:
                failures = self.db.execute(
                    "SELECT count(*) FROM webhook_deliveries WHERE subscription_id=(SELECT subscription_id FROM webhook_deliveries WHERE id=?) AND status='failed'",
                    (ident,),
                ).fetchone()[0]
                if failures >= 5:
                    self.db.execute(
                        "UPDATE webhook_subscriptions SET active=0 WHERE id=(SELECT subscription_id FROM webhook_deliveries WHERE id=?)",
                        (ident,),
                    )


    def recover_stale(self, stale_before: float) -> int:
        with self.lock, self.db:
            return self.db.execute(
                "UPDATE webhook_deliveries SET status='queued',next_attempt_at=?,last_error='dispatcher lease expired',sending_started_at=NULL WHERE status='sending' AND sending_started_at<?",
                (time.time(), stale_before),
            ).rowcount

    def set_active(
        self,
        ident: str,
        principal: str,
        active: bool,
        cooldown_seconds: int = 0,
    ) -> bool:
        with self.lock, self.db:
            if active and cooldown_seconds > 0:
                latest = self.db.execute(
                    "SELECT created_at FROM webhook_deliveries WHERE subscription_id=? AND status='failed' ORDER BY created_at DESC LIMIT 1",
                    (ident,),
                ).fetchone()
                if latest is not None:
                    failed_at = datetime.fromisoformat(latest[0])
                    age = (datetime.now(timezone.utc) - failed_at).total_seconds()
                    if age < cooldown_seconds:
                        raise ValueError(f"circuit breaker cooldown has {int(cooldown_seconds-age)} seconds remaining")
            return bool(self.db.execute(
                "UPDATE webhook_subscriptions SET active=? WHERE id=? AND principal=?",
                (int(active), ident, principal),
            ).rowcount)

    def attempt_timeline(self, delivery_id: str, principal: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                """SELECT a.* FROM webhook_attempts a JOIN webhook_deliveries d ON d.id=a.delivery_id
                JOIN webhook_subscriptions s ON s.id=d.subscription_id
                WHERE a.delivery_id=? AND s.principal=? ORDER BY a.id""",
                (delivery_id, principal),
            ).fetchall()
        return [dict(row) for row in rows]

    def health(self, ident: str, principal: str) -> dict | None:
        with self.lock:
            subscription = self.db.execute(
                "SELECT id,url,events,active,created_at FROM webhook_subscriptions WHERE id=? AND principal=?",
                (ident, principal),
            ).fetchone()
            if subscription is None:
                return None
            rows = self.db.execute(
                "SELECT status,count(*) AS count FROM webhook_deliveries WHERE subscription_id=? GROUP BY status",
                (ident,),
            ).fetchall()
            latest = self.db.execute(
                "SELECT status,response_status,last_error,created_at FROM webhook_deliveries WHERE subscription_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (ident,),
            ).fetchone()
        counts = {status: 0 for status in ("queued","sending","delivered","failed")}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return {**dict(subscription), "counts": counts, "latest": dict(latest) if latest else None}

    def rotate_secret(self, ident: str, principal: str) -> str | None:
        secret = secrets.token_urlsafe(32)
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE webhook_subscriptions SET secret=? WHERE id=? AND principal=? AND active=1",
                (self._encrypt_secret(ident, secret), ident, principal),
            ).rowcount
        return secret if changed else None

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM webhook_subscriptions LIMIT 1").fetchall() is not None

    def active_count(self, principal: str) -> int:
        """Active subscriptions owned by ``principal`` (plan limits and usage)."""
        with self.lock:
            return int(self.db.execute(
                "SELECT count(*) FROM webhook_subscriptions WHERE principal=? AND active=1", (principal,)).fetchone()[0])

    def list_subscriptions(self, principal: str, limit: int = 100, cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Principal-owned subscriptions, newest first, without secrets. Raises ValueError on a bad cursor."""
        clauses, parameters = ["principal=?"], [principal]
        if cursor:
            cursor_time, cursor_id = decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND id<?))")
            parameters.extend((cursor_time, cursor_time, cursor_id))
        page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
        with self.lock:
            rows = self.db.execute(
                f"SELECT id,url,events,active,created_at FROM webhook_subscriptions WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,id DESC LIMIT ?",
                tuple(parameters)).fetchall()
        items = [dict(row) for row in rows[:page_size]]
        return items, (encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None)

    def enqueue_test(self, ident: str, principal: str) -> str | None:
        """Queue a ``webhook.test`` delivery to one active owned subscription; None if not found."""
        event_id = f"test:{uuid.uuid4().hex}"
        payload = json.dumps({"webhook_id": ident, "test": True}, sort_keys=True, separators=(",", ":"))
        with self.lock, self.db:
            owned = self.db.execute("SELECT 1 FROM webhook_subscriptions WHERE id=? AND principal=? AND active=1", (ident, principal)).fetchone()
            if owned is None:
                return None
            self.db.execute(
                "INSERT INTO webhook_deliveries(id,subscription_id,event_id,event_type,payload,payload_sha256,status,next_attempt_at,created_at) VALUES(?,?,?,?,?,?,'queued',?,?)",
                (uuid.uuid4().hex, ident, event_id, "webhook.test", payload, hashlib.sha256(payload.encode()).hexdigest(), time.time(), datetime.now(timezone.utc).isoformat()))
        return event_id

    def delivery_metrics(self) -> dict[str, int]:
        with self.lock:
            rows = self.db.execute("SELECT status,count(*) AS count FROM webhook_deliveries GROUP BY status").fetchall()
        counts = {status: 0 for status in ("queued", "sending", "delivered", "failed")}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return counts

    def list_deliveries(self, principal: str, status: str | None = None, after: str | None = None,
                        limit: int = 100, cursor: str | None = None) -> tuple[list[dict], str | None]:
        clauses, parameters = ["s.principal=?"], [principal]
        if status:
            clauses.append("d.status=?"); parameters.append(status)
        if after:
            clauses.append("d.id>?"); parameters.append(after)
        if cursor:
            cursor_time, cursor_id = decode_cursor(cursor)
            clauses.append("(d.created_at<? OR (d.created_at=? AND d.id<?))")
            parameters.extend((cursor_time, cursor_time, cursor_id))
        page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
        query = f"""SELECT d.id,d.subscription_id,d.event_id,d.event_type,d.payload_sha256,d.status,d.attempts,
            d.next_attempt_at,d.response_status,d.last_error,d.created_at
            FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id
            WHERE {' AND '.join(clauses)} ORDER BY d.created_at DESC,d.id DESC LIMIT ?"""
        with self.lock:
            rows = self.db.execute(query, tuple(parameters)).fetchall()
        items = [dict(row) for row in rows[:page_size]]
        return items, (encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None)

    def get_delivery(self, principal: str, delivery_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("""SELECT d.id,d.subscription_id,d.event_id,d.event_type,d.payload_sha256,d.status,d.attempts,
                d.next_attempt_at,d.response_status,d.last_error,d.created_at
                FROM webhook_deliveries d JOIN webhook_subscriptions s ON s.id=d.subscription_id
                WHERE d.id=? AND s.principal=?""", (delivery_id, principal)).fetchone()
        return dict(row) if row else None

    def replay_delivery(self, principal: str, delivery_id: str) -> bool:
        with self.lock, self.db:
            return bool(self.db.execute(
                """UPDATE webhook_deliveries SET status='queued',next_attempt_at=?,last_error=NULL
                WHERE id=? AND status='failed' AND subscription_id IN
                (SELECT id FROM webhook_subscriptions WHERE principal=? AND active=1)""",
                (time.time(), delivery_id, principal)).rowcount)

    def cleanup_deliveries(self, delivered_before: str, failed_before: str) -> dict[str, int]:
        with self.lock, self.db:
            self.db.execute("""DELETE FROM webhook_attempts WHERE delivery_id IN (SELECT id FROM webhook_deliveries
                WHERE (status='delivered' AND created_at<?) OR (status='failed' AND created_at<?))""", (delivered_before, failed_before))
            delivered = self.db.execute("DELETE FROM webhook_deliveries WHERE status='delivered' AND created_at<?", (delivered_before,)).rowcount
            failed = self.db.execute("DELETE FROM webhook_deliveries WHERE status='failed' AND created_at<?", (failed_before,)).rowcount
        return {"delivered": delivered, "failed": failed}

    def operational_metrics(self) -> dict[str, float]:
        with self.lock:
            terminal = self.db.execute("SELECT status,count(*) FROM webhook_deliveries WHERE status IN ('delivered','failed') GROUP BY status").fetchall()
            queued = self.db.execute("SELECT min(created_at) FROM webhook_deliveries WHERE status='queued'").fetchone()[0]
            suspended = self.db.execute("SELECT count(*) FROM webhook_subscriptions WHERE active=0").fetchone()[0]
        return _operational({row[0]: int(row[1]) for row in terminal}, queued, suspended)

    def delete_principal(self, principal: str) -> dict[str, int]:
        """Hard-delete a principal's subscriptions, queued/finished deliveries and attempt logs."""
        if not principal:
            raise ValueError("principal is required")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                subscriptions = [row[0] for row in self.db.execute("SELECT id FROM webhook_subscriptions WHERE principal=?", (principal,))]
                deliveries = attempts = 0
                for subscription in subscriptions:
                    delivery_ids = [row[0] for row in self.db.execute("SELECT id FROM webhook_deliveries WHERE subscription_id=?", (subscription,))]
                    for delivery in delivery_ids:
                        attempts += self.db.execute("DELETE FROM webhook_attempts WHERE delivery_id=?", (delivery,)).rowcount
                    deliveries += self.db.execute("DELETE FROM webhook_deliveries WHERE subscription_id=?", (subscription,)).rowcount
                removed = self.db.execute("DELETE FROM webhook_subscriptions WHERE principal=?", (principal,)).rowcount
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return {"webhook_subscriptions": removed, "webhook_deliveries": deliveries, "webhook_attempts": attempts}


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
        headers = json.loads(delivery["headers"] or "{}")
        headers.update({
            "Content-Type":"application/json",
            "X-Meemee-Event":delivery["event_type"],
            "X-Meemee-Delivery":delivery["id"],
            "X-Meemee-Timestamp":timestamp,
            "X-Meemee-Signature-256":self.signature(delivery["secret"], timestamp, delivery["payload"]),
        })
        try:
            response = await self.client.post(delivery["url"], content=delivery["payload"], headers=headers)
            if 200 <= response.status_code < 300:
                self.store.succeed(delivery["id"], response.status_code)
            else:
                self.store.retry(delivery["id"], f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            self.store.retry(delivery["id"], type(exc).__name__)
        return True


async def dispatch_forever(store: WebhookStore, poll_seconds: float = 1.0) -> None:
    """Deliver queued webhooks continuously. Safe to run in multiple processes."""
    dispatcher = WebhookDispatcher(store)
    store.recover_stale(time.time() - 300)
    deliveries = 0
    try:
        while True:
            delivered = await dispatcher.deliver_one()
            deliveries += int(delivered)
            if deliveries and deliveries % 1000 == 0:
                store.recover_stale(time.time() - 300)
            if not delivered:
                import asyncio

                await asyncio.sleep(poll_seconds)
    finally:
        await dispatcher.client.aclose()


def _operational(counts: dict[str, int], oldest_queued: str | None, suspended: int) -> dict[str, float]:
    total = counts.get("delivered", 0) + counts.get("failed", 0)
    success = counts.get("delivered", 0) / total if total else 1.0
    age = 0.0
    if oldest_queued:
        age = max((datetime.now(timezone.utc) - datetime.fromisoformat(oldest_queued)).total_seconds(), 0.0)
    return {"success_rate": success, "oldest_queued_seconds": age, "suspended": float(suspended)}


# Module-level helpers kept for existing callers; they work with either backend's store.
def delivery_metrics(store) -> dict[str, int]:
    """Return exact outbox counts by delivery status."""
    return store.delivery_metrics()


def list_deliveries(store, principal: str, status: str | None = None, after: str | None = None,
                    limit: int = 100, cursor: str | None = None) -> tuple[list[dict], str | None]:
    """List principal-owned deliveries without signing secrets or payload bodies."""
    return store.list_deliveries(principal, status, after, limit, cursor)


def replay_delivery(store, principal: str, delivery_id: str) -> bool:
    """Requeue a failed principal-owned delivery without resetting attempt history."""
    return store.replay_delivery(principal, delivery_id)


def cleanup_deliveries(store, delivered_before: str, failed_before: str) -> dict[str, int]:
    """Delete old terminal delivery records; queued/sending rows are never touched."""
    return store.cleanup_deliveries(delivered_before, failed_before)


def operational_metrics(store) -> dict[str, float]:
    return store.operational_metrics()

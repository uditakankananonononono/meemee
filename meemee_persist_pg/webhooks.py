"""PostgreSQL webhook subscriptions and delivery outbox: the same contract as ``meemee.webhooks.WebhookStore``.

With per-host webhooks.sqlite3, a subscription created through one API host was unknown to the
others: jobs finished by a worker on another host never queued its deliveries, and listing,
pausing, rotating or deleting it through another host returned nothing or 404. Here every host and
every dispatcher share one outbox; ``claim`` uses ``FOR UPDATE SKIP LOCKED`` so concurrent
dispatchers never send the same delivery twice. Secrets are encrypted with the same key and
context as SQLite, so a cutover copies them unchanged.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from meemee.cursors import decode_cursor, encode_cursor
from meemee.secret_cipher import SecretCipher
from meemee.webhooks import RESERVED_HEADERS, _operational, validate_webhook_url

from ._db import Database

_DELIVERY_COLUMNS = """d.id,d.subscription_id,d.event_id,d.event_type,d.payload_sha256,d.status,d.attempts,
    d.next_attempt_at,d.response_status,d.last_error,d.created_at"""


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row(row: dict[str, Any]) -> dict:
    result = dict(row)
    for key in ("created_at", "started_at", "finished_at"):
        if isinstance(result.get(key), datetime):
            result[key] = _iso(result[key])
    if isinstance(result.get("active"), bool):
        result["active"] = int(result["active"])
    return result


class WebhookStore:
    """Durable webhook subscriptions and transactional delivery outbox (PostgreSQL)."""

    def __init__(self, db: Database, max_payload_bytes: int = 256_000, encryption_key: str | None = None):
        if max_payload_bytes < 1024:
            raise ValueError("webhook payload ceiling must be at least 1024 bytes")
        if not encryption_key:
            raise ValueError("webhook secret encryption requires MEEMEE_VAULT_KEY")
        self.db, self.max_payload_bytes = db, max_payload_bytes
        self.secret_cipher = SecretCipher(encryption_key)
        self._check_secrets()

    def _secret_context(self, ident: str) -> str:
        return f"webhook-subscription:{ident}"

    def _check_secrets(self) -> None:
        """Fail closed at startup if the configured key cannot read stored secrets."""
        with self.db.transaction() as c:
            row = c.execute("SELECT id,secret FROM meemee_webhook_subscriptions ORDER BY created_at LIMIT 1").fetchone()
        if row is not None:
            self.secret_cipher.decrypt(row["secret"], self._secret_context(row["id"]))

    def subscribe(self, principal: str, url: str, events: set[str], fields: set[str] | None = None,
                  headers: dict[str, str] | None = None) -> tuple[str, str]:
        validate_webhook_url(url)
        if not events or any(not event.strip() for event in events):
            raise ValueError("at least one non-empty event is required")
        headers = headers or {}
        if any(name.lower() in RESERVED_HEADERS or name.lower().startswith("proxy-") for name in headers):
            raise ValueError("custom headers contain a forbidden sensitive or transport name")
        if len(headers) > 20 or any(len(name) > 100 or len(value) > 1000 for name, value in headers.items()):
            raise ValueError("custom headers exceed count or size limits")
        ident, secret = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_webhook_subscriptions(id,principal,url,secret,events,fields,headers,active,created_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s)""",
                      (ident, principal, url, self.secret_cipher.encrypt(secret, self._secret_context(ident)),
                       " ".join(sorted(events)), " ".join(sorted(fields or ())) or None,
                       json.dumps(headers, sort_keys=True) if headers else None, datetime.now(timezone.utc)))
        return ident, secret

    def unsubscribe(self, ident: str, principal: str) -> bool:
        with self.db.transaction() as c:
            return bool(c.execute("UPDATE meemee_webhook_subscriptions SET active=false WHERE id=%s AND principal=%s AND active",
                                  (ident, principal)).rowcount)

    def enqueue(self, event_id: str, event_type: str, payload: dict, *, principal: str) -> int:
        """Queue one delivery per matching active subscription owned by ``principal`` (the event's owner)."""
        envelope = {"schema": "meemee.webhook.v1", "event_id": event_id, "event_type": event_type, "data": payload}
        encoded, now = json.dumps(envelope, sort_keys=True, separators=(",", ":")), time.time()
        if len(encoded.encode()) > self.max_payload_bytes:
            raise ValueError(f"webhook payload exceeds {self.max_payload_bytes} bytes")
        created = 0
        with self.db.transaction() as c:
            subscriptions = c.execute("SELECT id,events,fields FROM meemee_webhook_subscriptions WHERE active AND principal=%s",
                                      (principal,)).fetchall()
            for subscription in subscriptions:
                wanted = subscription["events"].split()
                if event_type not in wanted and "*" not in wanted:
                    continue
                body = encoded
                if subscription["fields"]:
                    selected = {key: payload[key] for key in subscription["fields"].split() if key in payload}
                    body = json.dumps({**envelope, "data": selected}, sort_keys=True, separators=(",", ":"))
                created += c.execute(
                    """INSERT INTO meemee_webhook_deliveries(id,subscription_id,event_id,event_type,payload,payload_sha256,status,next_attempt_at,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'queued',%s,%s) ON CONFLICT (subscription_id,event_id) DO NOTHING""",
                    (uuid.uuid4().hex, subscription["id"], event_id, event_type, body, hashlib.sha256(body.encode()).hexdigest(),
                     now, datetime.now(timezone.utc))).rowcount
        return created

    def claim(self, now: float | None = None) -> dict | None:
        current = time.time() if now is None else now
        with self.db.transaction() as c:
            row = c.execute("""SELECT d.*,s.url,s.secret,s.headers FROM meemee_webhook_deliveries d
                JOIN meemee_webhook_subscriptions s ON s.id=d.subscription_id
                WHERE d.status='queued' AND d.next_attempt_at<=%s AND s.active
                ORDER BY d.next_attempt_at,d.id LIMIT 1 FOR UPDATE OF d SKIP LOCKED""", (current,)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE meemee_webhook_deliveries SET status='sending',attempts=attempts+1,sending_started_at=%s WHERE id=%s",
                      (current, row["id"]))
            c.execute("INSERT INTO meemee_webhook_attempts(delivery_id,attempt,started_at) VALUES (%s,%s,%s)",
                      (row["id"], row["attempts"] + 1, datetime.now(timezone.utc)))
        delivery = _row(row)
        delivery["secret"] = self.secret_cipher.decrypt(delivery["secret"], self._secret_context(delivery["subscription_id"]))
        return delivery

    def succeed(self, ident: str, status: int) -> None:
        with self.db.transaction() as c:
            c.execute("""UPDATE meemee_webhook_deliveries SET status='delivered',response_status=%s,last_error=NULL,sending_started_at=NULL
                         WHERE id=%s AND status='sending'""", (status, ident))
            c.execute("""UPDATE meemee_webhook_attempts SET finished_at=%s,outcome='delivered',response_status=%s
                         WHERE id=(SELECT max(id) FROM meemee_webhook_attempts WHERE delivery_id=%s)""",
                      (datetime.now(timezone.utc), status, ident))

    def retry(self, ident: str, error: str, max_attempts: int = 8) -> None:
        with self.db.transaction() as c:
            row = c.execute("SELECT attempts,subscription_id FROM meemee_webhook_deliveries WHERE id=%s FOR UPDATE", (ident,)).fetchone()
            if row is None:
                return
            terminal = row["attempts"] >= max_attempts
            delay = min(2 ** max(row["attempts"] - 1, 0), 3600)
            status = "failed" if terminal else "queued"
            c.execute("""UPDATE meemee_webhook_deliveries SET status=%s,next_attempt_at=%s,last_error=%s,sending_started_at=NULL WHERE id=%s""",
                      (status, time.time() + delay, error[:1000], ident))
            c.execute("""UPDATE meemee_webhook_attempts SET finished_at=%s,outcome=%s,error=%s
                         WHERE id=(SELECT max(id) FROM meemee_webhook_attempts WHERE delivery_id=%s)""",
                      (datetime.now(timezone.utc), status, error[:1000], ident))
            if terminal:
                failures = c.execute("SELECT count(*) AS n FROM meemee_webhook_deliveries WHERE subscription_id=%s AND status='failed'",
                                     (row["subscription_id"],)).fetchone()["n"]
                if failures >= 5:
                    c.execute("UPDATE meemee_webhook_subscriptions SET active=false WHERE id=%s", (row["subscription_id"],))

    def recover_stale(self, stale_before: float) -> int:
        with self.db.transaction() as c:
            return c.execute("""UPDATE meemee_webhook_deliveries SET status='queued',next_attempt_at=%s,
                                last_error='dispatcher lease expired',sending_started_at=NULL
                                WHERE status='sending' AND sending_started_at<%s""", (time.time(), stale_before)).rowcount

    def set_active(self, ident: str, principal: str, active: bool, cooldown_seconds: int = 0) -> bool:
        with self.db.transaction() as c:
            if active and cooldown_seconds > 0:
                latest = c.execute("""SELECT created_at FROM meemee_webhook_deliveries WHERE subscription_id=%s AND status='failed'
                                      ORDER BY created_at DESC LIMIT 1""", (ident,)).fetchone()
                if latest is not None:
                    age = (datetime.now(timezone.utc) - latest["created_at"]).total_seconds()
                    if age < cooldown_seconds:
                        raise ValueError(f"circuit breaker cooldown has {int(cooldown_seconds-age)} seconds remaining")
            return bool(c.execute("UPDATE meemee_webhook_subscriptions SET active=%s WHERE id=%s AND principal=%s",
                                  (active, ident, principal)).rowcount)

    def attempt_timeline(self, delivery_id: str, principal: str) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT a.* FROM meemee_webhook_attempts a JOIN meemee_webhook_deliveries d ON d.id=a.delivery_id
                JOIN meemee_webhook_subscriptions s ON s.id=d.subscription_id
                WHERE a.delivery_id=%s AND s.principal=%s ORDER BY a.id""", (delivery_id, principal)).fetchall()
        return [_row(row) for row in rows]

    def health(self, ident: str, principal: str) -> dict | None:
        with self.db.transaction() as c:
            subscription = c.execute("SELECT id,url,events,active,created_at FROM meemee_webhook_subscriptions WHERE id=%s AND principal=%s",
                                     (ident, principal)).fetchone()
            if subscription is None:
                return None
            rows = c.execute("SELECT status,count(*) AS count FROM meemee_webhook_deliveries WHERE subscription_id=%s GROUP BY status",
                             (ident,)).fetchall()
            latest = c.execute("""SELECT status,response_status,last_error,created_at FROM meemee_webhook_deliveries
                                  WHERE subscription_id=%s ORDER BY created_at DESC,id DESC LIMIT 1""", (ident,)).fetchone()
        counts = {status: 0 for status in ("queued", "sending", "delivered", "failed")}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return {**_row(subscription), "counts": counts, "latest": _row(latest) if latest else None}

    def rotate_secret(self, ident: str, principal: str) -> str | None:
        secret = secrets.token_urlsafe(32)
        with self.db.transaction() as c:
            changed = c.execute("UPDATE meemee_webhook_subscriptions SET secret=%s WHERE id=%s AND principal=%s AND active",
                                (self.secret_cipher.encrypt(secret, self._secret_context(ident)), ident, principal)).rowcount
        return secret if changed else None

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_webhook_subscriptions LIMIT 0")
        return True

    def active_count(self, principal: str) -> int:
        with self.db.transaction() as c:
            return int(c.execute("SELECT count(*) AS n FROM meemee_webhook_subscriptions WHERE principal=%s AND active",
                                 (principal,)).fetchone()["n"])

    def list_subscriptions(self, principal: str, limit: int = 100, cursor: str | None = None) -> tuple[list[dict], str | None]:
        clauses, parameters = ["principal=%s"], [principal]
        if cursor:
            cursor_time, cursor_id = decode_cursor(cursor)
            moment = _moment(cursor_time)
            clauses.append("(created_at<%s OR (created_at=%s AND id<%s))")
            parameters.extend((moment, moment, cursor_id))
        page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
        with self.db.transaction() as c:
            rows = c.execute(f"""SELECT id,url,events,active,created_at FROM meemee_webhook_subscriptions
                                 WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,id DESC LIMIT %s""", tuple(parameters)).fetchall()
        items = [_row(row) for row in rows[:page_size]]
        return items, (encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None)

    def enqueue_test(self, ident: str, principal: str) -> str | None:
        event_id = f"test:{uuid.uuid4().hex}"
        payload = json.dumps({"webhook_id": ident, "test": True}, sort_keys=True, separators=(",", ":"))
        with self.db.transaction() as c:
            if c.execute("SELECT 1 FROM meemee_webhook_subscriptions WHERE id=%s AND principal=%s AND active", (ident, principal)).fetchone() is None:
                return None
            c.execute("""INSERT INTO meemee_webhook_deliveries(id,subscription_id,event_id,event_type,payload,payload_sha256,status,next_attempt_at,created_at)
                         VALUES (%s,%s,%s,'webhook.test',%s,%s,'queued',%s,%s)""",
                      (uuid.uuid4().hex, ident, event_id, payload, hashlib.sha256(payload.encode()).hexdigest(), time.time(),
                       datetime.now(timezone.utc)))
        return event_id

    def delivery_metrics(self) -> dict[str, int]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT status,count(*) AS count FROM meemee_webhook_deliveries GROUP BY status").fetchall()
        counts = {status: 0 for status in ("queued", "sending", "delivered", "failed")}
        counts.update({row["status"]: int(row["count"]) for row in rows})
        return counts

    def list_deliveries(self, principal: str, status: str | None = None, after: str | None = None,
                        limit: int = 100, cursor: str | None = None) -> tuple[list[dict], str | None]:
        clauses, parameters = ["s.principal=%s"], [principal]
        if status:
            clauses.append("d.status=%s"); parameters.append(status)
        if after:
            clauses.append("d.id>%s"); parameters.append(after)
        if cursor:
            cursor_time, cursor_id = decode_cursor(cursor)
            moment = _moment(cursor_time)
            clauses.append("(d.created_at<%s OR (d.created_at=%s AND d.id<%s))")
            parameters.extend((moment, moment, cursor_id))
        page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
        with self.db.transaction() as c:
            rows = c.execute(f"""SELECT {_DELIVERY_COLUMNS} FROM meemee_webhook_deliveries d
                JOIN meemee_webhook_subscriptions s ON s.id=d.subscription_id
                WHERE {' AND '.join(clauses)} ORDER BY d.created_at DESC,d.id DESC LIMIT %s""", tuple(parameters)).fetchall()
        items = [_row(row) for row in rows[:page_size]]
        return items, (encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None)

    def get_delivery(self, principal: str, delivery_id: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute(f"""SELECT {_DELIVERY_COLUMNS} FROM meemee_webhook_deliveries d
                JOIN meemee_webhook_subscriptions s ON s.id=d.subscription_id WHERE d.id=%s AND s.principal=%s""",
                            (delivery_id, principal)).fetchone()
        return _row(row) if row else None

    def replay_delivery(self, principal: str, delivery_id: str) -> bool:
        with self.db.transaction() as c:
            return bool(c.execute("""UPDATE meemee_webhook_deliveries SET status='queued',next_attempt_at=%s,last_error=NULL
                WHERE id=%s AND status='failed' AND subscription_id IN
                (SELECT id FROM meemee_webhook_subscriptions WHERE principal=%s AND active)""",
                                  (time.time(), delivery_id, principal)).rowcount)

    def cleanup_deliveries(self, delivered_before: str, failed_before: str) -> dict[str, int]:
        delivered_at, failed_at = _moment(delivered_before), _moment(failed_before)
        with self.db.transaction() as c:
            c.execute("""DELETE FROM meemee_webhook_attempts WHERE delivery_id IN (SELECT id FROM meemee_webhook_deliveries
                WHERE (status='delivered' AND created_at<%s) OR (status='failed' AND created_at<%s))""", (delivered_at, failed_at))
            delivered = c.execute("DELETE FROM meemee_webhook_deliveries WHERE status='delivered' AND created_at<%s", (delivered_at,)).rowcount
            failed = c.execute("DELETE FROM meemee_webhook_deliveries WHERE status='failed' AND created_at<%s", (failed_at,)).rowcount
        return {"delivered": delivered, "failed": failed}

    def operational_metrics(self) -> dict[str, float]:
        with self.db.transaction() as c:
            terminal = c.execute("""SELECT status,count(*) AS n FROM meemee_webhook_deliveries
                                    WHERE status IN ('delivered','failed') GROUP BY status""").fetchall()
            queued = c.execute("SELECT min(created_at) AS t FROM meemee_webhook_deliveries WHERE status='queued'").fetchone()["t"]
            suspended = c.execute("SELECT count(*) AS n FROM meemee_webhook_subscriptions WHERE NOT active").fetchone()["n"]
        return _operational({row["status"]: int(row["n"]) for row in terminal}, _iso(queued), suspended)

    def delete_principal(self, principal: str) -> dict[str, int]:
        """Hard-delete a principal's subscriptions, deliveries and attempt logs in one transaction."""
        if not principal:
            raise ValueError("principal is required")
        with self.db.transaction() as c:
            owned = "SELECT id FROM meemee_webhook_subscriptions WHERE principal=%s"
            attempts = c.execute(f"""DELETE FROM meemee_webhook_attempts WHERE delivery_id IN
                (SELECT id FROM meemee_webhook_deliveries WHERE subscription_id IN ({owned}))""", (principal,)).rowcount
            deliveries = c.execute(f"DELETE FROM meemee_webhook_deliveries WHERE subscription_id IN ({owned})", (principal,)).rowcount
            removed = c.execute("DELETE FROM meemee_webhook_subscriptions WHERE principal=%s", (principal,)).rowcount
        return {"webhook_subscriptions": removed, "webhook_deliveries": deliveries, "webhook_attempts": attempts}

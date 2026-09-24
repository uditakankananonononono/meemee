from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


class EmailVerificationStore:
    """One-time, expiring email verification challenges stored as digests."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS email_verifications(
                    account_id TEXT PRIMARY KEY, token_digest BLOB NOT NULL,
                    expires_at TEXT NOT NULL, verified_at TEXT, sent_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS password_resets(
                    account_id TEXT PRIMARY KEY, token_digest BLOB NOT NULL,
                    expires_at TEXT NOT NULL, used_at TEXT, sent_at TEXT NOT NULL
                );
            """)

    def issue(self, account_id: str, ttl_minutes: int = 30) -> str:
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO email_verifications VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET token_digest=excluded.token_digest,expires_at=excluded.expires_at,verified_at=NULL,sent_at=excluded.sent_at",
                (account_id, hashlib.sha256(raw.encode()).digest(), (now + timedelta(minutes=ttl_minutes)).isoformat(), None, now.isoformat()),
            )
        return raw

    def verify(self, account_id: str, raw: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(raw.encode()).digest()
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM email_verifications WHERE account_id=?", (account_id,)).fetchone()
            if row is None or row["verified_at"] or row["expires_at"] <= now or not secrets.compare_digest(row["token_digest"], digest):
                return False
            self.db.execute("UPDATE email_verifications SET verified_at=? WHERE account_id=?", (now, account_id))
        return True

    def status(self, account_id: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT verified_at FROM email_verifications WHERE account_id=?", (account_id,)).fetchone()
        return bool(row and row["verified_at"])

    def issue_password_reset(self, account_id: str, ttl_minutes: int = 20) -> str:
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO password_resets VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET token_digest=excluded.token_digest,expires_at=excluded.expires_at,used_at=NULL,sent_at=excluded.sent_at",
                (account_id, hashlib.sha256(raw.encode()).digest(), (now + timedelta(minutes=ttl_minutes)).isoformat(), None, now.isoformat()),
            )
        return raw

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() is not None

    def consume_password_reset(self, account_id: str, raw: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(raw.encode()).digest()
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM password_resets WHERE account_id=?", (account_id,)).fetchone()
            if row is None or row["used_at"] or row["expires_at"] <= now or not secrets.compare_digest(row["token_digest"], digest):
                return False
            self.db.execute("UPDATE password_resets SET used_at=? WHERE account_id=?", (now, account_id))
        return True


class ResendMailer:
    def __init__(self, api_key: str | None, from_address: str, public_url: str, api_url: str = "https://api.resend.com"):
        self.api_key, self.from_address, self.public_url = api_key, from_address, public_url.rstrip("/")
        self.api_url = api_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.from_address and self.public_url.startswith("https://"))

    def _send(self, recipient: str, subject: str, html: str, reply_to: str | None = None) -> str:
        if not self.configured:
            raise RuntimeError("transactional email is not configured")
        response = httpx.post(
            f"{self.api_url}/emails",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={k: v for k, v in {"from": self.from_address, "to": [recipient], "subject": subject, "html": html, "reply_to": reply_to}.items() if v},
            timeout=15,
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def send_verification(self, recipient: str, account_id: str, token: str) -> str:
        url = f"{self.public_url}/app/?verify={token}&account={account_id}"
        return self._send(recipient, "Verify your Meemee email", f'<p>Confirm your Meemee email:</p><p><a href="{url}">Verify email</a></p><p>This link expires in 30 minutes.</p>')

    def send_password_reset(self, recipient: str, account_id: str, token: str) -> str:
        url = f"{self.public_url}/app/?reset={token}&account={account_id}"
        return self._send(recipient, "Reset your Meemee password", f'<p>Reset your Meemee password:</p><p><a href="{url}">Choose a new password</a></p><p>This link expires in 20 minutes. If you did not request it, ignore this email.</p>')


    def send_task(self, recipient: str, subject: str, body: str, reply_to: str | None = None) -> str:
        if not subject.strip() or not body.strip():
            raise ValueError("subject and body are required")
        safe_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        return self._send(recipient, subject.strip(), f"<p>{safe_body}</p>", reply_to=reply_to)

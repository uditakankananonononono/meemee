from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from ._db import Database

SCOPES = ["runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"]


class AccountStore:
    """Multi-instance account, session and recovery state on PostgreSQL."""

    def __init__(self, db: Database): self.db = db

    @staticmethod
    def _password(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)

    @staticmethod
    def _digest(value: str) -> bytes: return hashlib.sha256(value.encode()).digest()

    def create_account(self, email: str, password: str, display_name: str) -> tuple[dict, str]:
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ValueError("enter a valid email address")
        if not 12 <= len(password) <= 200: raise ValueError("password must be 12-200 characters")
        account_id, salt = "acct_" + secrets.token_hex(12), secrets.token_bytes(16)
        now = datetime.now(timezone.utc); token_id, raw = secrets.token_hex(12), f"mee_{secrets.token_urlsafe(32)}"
        with self.db.transaction() as c:
            c.execute("INSERT INTO meemee_accounts(id,email,display_name,password_hash,password_salt) VALUES(%s,%s,%s,%s,%s)",(account_id,email,display_name.strip(),self._password(password,salt),salt))
            c.execute("INSERT INTO meemee_api_tokens(id,name,digest,scopes,expires_at,owner_id,token_kind) VALUES(%s,%s,%s,%s,%s,%s,'session')",(token_id,display_name.strip(),self._digest(raw),SCOPES,now+timedelta(days=30),account_id))
        return {"id":account_id,"email":email,"display_name":display_name.strip(),"created_at":now.isoformat()},raw

    def login_account(self, email: str, password: str) -> tuple[dict, str] | None:
        now=datetime.now(timezone.utc)
        with self.db.transaction() as c:
            row=c.execute("SELECT * FROM meemee_accounts WHERE email=%s AND disabled_at IS NULL FOR UPDATE",(email.strip().lower(),)).fetchone()
            candidate=self._password(password,bytes(row["password_salt"]) if row else b"0"*16)
            expected=bytes(row["password_hash"]) if row else b"0"*32
            if row and row["locked_until"] and row["locked_until"]>now: return None
            if not row or not hmac.compare_digest(candidate,expected):
                if row:
                    failures=row["failed_logins"]+1; locked=now+timedelta(minutes=15) if failures>=5 else None
                    c.execute("UPDATE meemee_accounts SET failed_logins=%s,locked_until=%s WHERE id=%s",(failures,locked,row["id"]))
                return None
            raw=f"mee_{secrets.token_urlsafe(32)}"; token_id=secrets.token_hex(12)
            c.execute("UPDATE meemee_accounts SET failed_logins=0,locked_until=NULL WHERE id=%s",(row["id"],))
            c.execute("INSERT INTO meemee_api_tokens(id,name,digest,scopes,expires_at,owner_id,token_kind) VALUES(%s,%s,%s,%s,%s,%s,'session')",(token_id,row["display_name"],self._digest(raw),SCOPES,now+timedelta(days=30),row["id"]))
        return {"id":row["id"],"email":row["email"],"display_name":row["display_name"],"created_at":row["created_at"].isoformat()},raw

    def issue_challenge(self, table: str, account_id: str, minutes: int) -> str:
        if table not in {"meemee_email_verifications","meemee_password_resets"}: raise ValueError("unknown challenge")
        raw=secrets.token_urlsafe(32)
        with self.db.transaction() as c:
            c.execute(f"INSERT INTO {table}(account_id,token_digest,expires_at) VALUES(%s,%s,clock_timestamp()+(%s * interval '1 minute')) ON CONFLICT(account_id) DO UPDATE SET token_digest=excluded.token_digest,expires_at=excluded.expires_at,sent_at=clock_timestamp(),"+("verified_at=NULL" if table.endswith("verifications") else "used_at=NULL"),(account_id,self._digest(raw),minutes))
        return raw

    def disable_account(self, account_id: str) -> bool:
        with self.db.transaction() as c:
            changed=c.execute("UPDATE meemee_accounts SET disabled_at=clock_timestamp() WHERE id=%s AND disabled_at IS NULL",(account_id,)).rowcount
            if changed: c.execute("UPDATE meemee_api_tokens SET revoked_at=clock_timestamp() WHERE owner_id=%s AND revoked_at IS NULL",(account_id,))
        return bool(changed)

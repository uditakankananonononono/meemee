from __future__ import annotations

import hashlib
import hmac
import secrets

from ._db import Database


class TokenStore:
    def __init__(self, db: Database): self.db=db
    @staticmethod
    def digest(token: str)->bytes: return hashlib.sha256(token.encode()).digest()
    def create(self,name:str,scopes:set[str],expires_at:str|None=None)->tuple[str,str]:
        if not name.strip() or not scopes: raise ValueError("token name and at least one scope are required")
        ident,token=secrets.token_hex(12),f"mee_{secrets.token_urlsafe(32)}"
        with self.db.transaction() as c: c.execute("INSERT INTO meemee_api_tokens(id,name,digest,scopes,expires_at) VALUES(%s,%s,%s,%s,%s)",(ident,name,self.digest(token),sorted(scopes),expires_at))
        return ident,token
    def authenticate(self,token:str):
        digest=self.digest(token)
        with self.db.transaction() as c:
            row=c.execute("""SELECT id,name,digest,scopes FROM meemee_api_tokens WHERE digest=%s AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>clock_timestamp()) FOR UPDATE""",(digest,)).fetchone()
            if not row or not hmac.compare_digest(bytes(row["digest"]),digest): return None
            c.execute("UPDATE meemee_api_tokens SET last_used_at=clock_timestamp() WHERE id=%s",(row["id"],))
        try:
            from meemee.auth import Principal
            return Principal(row["id"],row["name"],frozenset(row["scopes"]))
        except ImportError: return {"id":row["id"],"name":row["name"],"scopes":frozenset(row["scopes"])}
    def introspect(self,token:str)->dict|None:
        """Redacted metadata and derived state for a raw token; never updates last_used_at."""
        digest=self.digest(token)
        with self.db.transaction() as c:
            row=c.execute("""SELECT id,name,digest,scopes,created_at,last_used_at,expires_at,revoked_at,owner_id,token_kind,
              clock_timestamp() AS now FROM meemee_api_tokens WHERE digest=%s""",(digest,)).fetchone()
        if not row or not hmac.compare_digest(bytes(row["digest"]),digest): return None
        from meemee.auth import token_introspection
        return token_introspection(dict(row),row["now"])
    def revoke(self,ident:str)->bool:
        with self.db.transaction() as c: return bool(c.execute("UPDATE meemee_api_tokens SET revoked_at=clock_timestamp() WHERE id=%s AND revoked_at IS NULL",(ident,)).rowcount)

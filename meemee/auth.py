from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException

from .config import Settings


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    expected = Settings().api_token
    if not expected:
        return
    scheme, _, supplied = (authorization or "").partition(" ")
    valid = scheme.lower() == "bearer" and hmac.compare_digest(
        hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )
    if not valid:
        raise HTTPException(status_code=401, detail="invalid bearer token")

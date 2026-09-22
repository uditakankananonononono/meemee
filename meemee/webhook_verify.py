from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    """Verify a Meemee webhook signature and reject stale replay attempts."""
    if tolerance_seconds < 0 or not signature.startswith("sha256="):
        return False
    try:
        observed = int(timestamp)
    except ValueError:
        return False
    current = int(time.time() if now is None else now)
    if abs(current - observed) > tolerance_seconds:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

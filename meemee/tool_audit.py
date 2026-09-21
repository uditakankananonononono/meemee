from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SENSITIVE_KEYS = re.compile(r"token|secret|password|api[_-]?key|authorization|cookie", re.IGNORECASE)


def redact(value: Any, key: str = "") -> Any:
    """Recursively redact secret-shaped fields while preserving an audit fingerprint."""
    if SENSITIVE_KEYS.search(key):
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
        return {"redacted": True, "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}
    if isinstance(value, dict):
        return {str(child): redact(item, str(child)) for child, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 20_000:
        encoded = value.encode()
        return {"truncated": True, "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded), "preview": value[:1000]}
    return value

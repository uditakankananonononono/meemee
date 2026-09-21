from __future__ import annotations

import hashlib
import re

PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:Bearer\s+)[A-Za-z0-9._~+/-]{16,}=*\b", re.IGNORECASE),
    re.compile(r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*([^\s,;]{8,})"),
)


def marker(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"[REDACTED:{digest}:{len(value)}]"


def scrub_text(text: str) -> str:
    """Remove common pasted credential forms while preserving stable correlation markers."""
    result = text
    for index, pattern in enumerate(PATTERNS):
        if index == len(PATTERNS) - 1:
            result = pattern.sub(lambda match: f"{match.group(1)}={marker(match.group(2))}", result)
        else:
            result = pattern.sub(lambda match: marker(match.group(0)), result)
    return result

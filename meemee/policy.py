from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import Risk


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    require_approval: bool


class PolicyEngine:
    """Deterministic deny-first policy over tools, risk, paths, hosts and arguments."""

    def __init__(self, document: dict[str, Any] | None = None):
        doc = document or {}
        self.denied_tools = set(doc.get("deny_tools", []))
        self.allowed_tools = set(doc.get("allow_tools", []))
        self.denied_paths = tuple(doc.get("deny_paths", []))
        self.denied_hosts = tuple(doc.get("deny_hosts", []))
        self.max_argument_bytes = int(doc.get("max_argument_bytes", 1_000_000))
        self.auto_approve = {Risk(value) for value in doc.get("auto_approve_risks", ["read"])}
        if self.max_argument_bytes < 1:
            raise ValueError("max_argument_bytes must be positive")

    @classmethod
    def from_file(cls, path: Path | None) -> PolicyEngine:
        if path is None:
            return cls()
        return cls(json.loads(path.read_text()))

    def evaluate(self, tool: str, arguments: dict[str, Any], risk: Risk) -> PolicyDecision:
        if tool in self.denied_tools:
            return PolicyDecision(False, "tool is explicitly denied", False)
        if self.allowed_tools and tool not in self.allowed_tools:
            return PolicyDecision(False, "tool is not in the allowlist", False)
        encoded = json.dumps(arguments, sort_keys=True, default=str).encode()
        if len(encoded) > self.max_argument_bytes:
            return PolicyDecision(False, "arguments exceed policy size limit", False)
        for key in ("path", "screenshot_path"):
            raw = arguments.get(key)
            if isinstance(raw, str) and any(fnmatch.fnmatch(raw, pattern) for pattern in self.denied_paths):
                return PolicyDecision(False, f"{key} matches a denied pattern", False)
        raw_url = arguments.get("url")
        if isinstance(raw_url, str):
            from urllib.parse import urlparse
            host = urlparse(raw_url).hostname or ""
            if any(fnmatch.fnmatch(host, pattern) for pattern in self.denied_hosts):
                return PolicyDecision(False, "URL host is denied", False)
        return PolicyDecision(True, "allowed", risk not in self.auto_approve)

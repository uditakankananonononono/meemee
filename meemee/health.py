from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import httpx


class ReadinessChecker:
    """Cached dependency checks with bounded latency and actionable component detail."""

    def __init__(self, data_dir: Path, database_checks: dict[str, Callable[[], object]], model_url: str, min_free_bytes: int = 100_000_000, cache_seconds: float = 2.0):
        self.data_dir = data_dir
        self.database_checks = database_checks
        self.model_url = model_url.rstrip("/")
        self.min_free_bytes = min_free_bytes
        self.cache_seconds = cache_seconds
        self._cached_at = 0.0
        self._cached: dict | None = None

    async def check(self, client: httpx.AsyncClient | None = None) -> dict:
        now = time.monotonic()
        if self._cached and now - self._cached_at < self.cache_seconds:
            return self._cached
        components: dict[str, dict] = {}
        for name, check in self.database_checks.items():
            try:
                check()
                components[name] = {"ok": True}
            except (OSError, RuntimeError, ValueError) as exc:
                components[name] = {"ok": False, "error": type(exc).__name__}
        usage = shutil.disk_usage(self.data_dir)
        components["disk"] = {"ok": usage.free >= self.min_free_bytes, "free_bytes": usage.free, "minimum_bytes": self.min_free_bytes}
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2))
        try:
            response = await client.get(f"{self.model_url}/models")
            components["model"] = {"ok": response.status_code < 500, "status": response.status_code}
        except httpx.HTTPError as exc:
            components["model"] = {"ok": False, "error": type(exc).__name__}
        finally:
            if owns_client:
                await client.aclose()
        result = {"status": "ready" if all(item["ok"] for item in components.values()) else "not_ready", "components": components}
        self._cached, self._cached_at = result, now
        return result

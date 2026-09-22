"""Retry policy matching Meemee server semantics.

The platform's own transport contract (current server model transport): bounded
retries for transient network/408/429/5xx failures, Retry-After support, jittered
exponential backoff, fail-fast permanent errors. The SDK applies the same rules to
API calls, with one addition: only idempotent methods are retried automatically,
because synchronous runs are not idempotent and queued jobs require an explicit idempotency key - retrying POST /v1/runs or an unkeyed /v1/jobs
could run the same goal twice.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded, jittered exponential backoff for idempotent requests.

    Defaults mirror the server's model transport: 3 attempts, exponential backoff
    with full jitter, Retry-After honoured on 429 responses.
    """

    #: Total attempts including the first try (server default: 3).
    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    backoff_max_seconds: float = 30.0
    #: Status codes considered transient (server semantics: 408/429/5xx).
    retry_statuses: frozenset[int] = field(default=frozenset({408, 429, 500, 502, 503, 504}))
    #: Methods retried automatically. POST is excluded on purpose: the server has
    #: unsafe POSTs are not retried; keyed job creation can be retried explicitly by the caller.
    retry_methods: frozenset[str] = field(default=frozenset({"GET", "HEAD", "DELETE"}))
    #: Honour the server's Retry-After header on 429 (and 503) responses.
    respect_retry_after: bool = True
    #: Cap on server-directed waits so a hostile Retry-After cannot stall the process.
    retry_after_max_seconds: float = 120.0

    def can_retry(self, method: str, attempt: int) -> bool:
        return method.upper() in self.retry_methods and attempt < self.max_attempts

    def is_retryable_status(self, status_code: int) -> bool:
        return status_code in self.retry_statuses

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait before attempt ``attempt + 1`` (attempt is 1-based).

        A server-directed Retry-After wins and is applied without jitter; otherwise
        full jitter over the exponential window, matching the server's model transport.
        """
        if retry_after is not None and self.respect_retry_after:
            return max(0.0, min(float(retry_after), self.retry_after_max_seconds))
        window = min(
            self.backoff_base_seconds * (self.backoff_multiplier ** (attempt - 1)),
            self.backoff_max_seconds,
        )
        return random.uniform(0.0, window)

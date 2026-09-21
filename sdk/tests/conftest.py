"""Shared fixtures: a mocked transport speaking the Meemee v0.16.0 contract."""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from meemee_client import MeemeeClient

BASE_URL = "http://testserver"
JOB_ID = "a1b2c3d4e5f6"
NOW = "2026-09-21T12:00:00+00:00"


class FakeSleeper:
    """Records requested sleep durations without sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    auth: Any = "mee_testtoken",
    sleeper: FakeSleeper | None = None,
    retry: Any = None,
) -> MeemeeClient:
    return MeemeeClient(
        BASE_URL,
        auth=auth,
        transport=httpx.MockTransport(handler),
        sleeper=sleeper or FakeSleeper(),
        retry=retry,
    )


def job_payload(status: str = "queued", *, result: Any = None, error: str | None = None) -> dict[str, Any]:
    return {
        "id": JOB_ID,
        "goal": "Summarise arXiv",
        "run_at": NOW,
        "status": status,
        "attempts": 1,
        "max_attempts": 3,
        "result": json.dumps(result) if result is not None else None,
        "error": error,
        "created_at": NOW,
        "updated_at": NOW,
    }


def event_dict(sequence: int, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "job_id": JOB_ID,
        "kind": kind,
        "payload": payload or {},
        "created_at": NOW,
    }


def sse_frame(sequence: int, kind: str, payload: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(event_dict(sequence, kind, payload), separators=(",", ":"))
    return f"id: {sequence}\nevent: {kind}\ndata: {data}\n\n".encode()


def sse_heartbeat(cursor: int) -> bytes:
    return f": heartbeat {cursor}\n\n".encode()


class FlakyStream(httpx.SyncByteStream):
    """Yields its chunks, then drops the connection mid-stream."""

    def __init__(self, chunks: list[bytes], exc: Exception) -> None:
        self._chunks = chunks
        self._exc = exc

    def __iter__(self):
        yield from self._chunks
        raise self._exc

    def close(self) -> None:
        pass


@pytest.fixture()
def sleeper() -> FakeSleeper:
    return FakeSleeper()

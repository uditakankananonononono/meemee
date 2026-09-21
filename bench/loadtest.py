#!/usr/bin/env python3
"""Meemee load & performance harness.

Boots a real local Meemee API server (uvicorn subprocess with a throwaway
MEEMEE_DATA_DIR), drives it over HTTP, and records machine-readable JSON
results under bench/results/.

Scenarios:
  1. job_create_throughput - concurrent POST /v1/jobs: latency + throughput.
  2. sse_fanout            - K concurrent SSE subscribers on one job; fan-out
                             latency of a terminal event after cancellation.
  3. concurrent_principals - P scoped tokens creating jobs concurrently;
                             per-principal quota isolation verification.
  4. quota_enforcement     - low daily quota: exactly `limit` creates succeed,
                             overflow is HTTP 429 with Retry-After: 86400, and
                             an admin override restores capacity.
  5. rate_limit            - low fixed window: exactly `limit` requests pass,
                             overflow is HTTP 429 with Retry-After in-window.
  6. retention_sweep       - seeded old data; times the `meemee retention-run`
                             operator CLI and verifies what was deleted/kept.

Everything in this directory is additive tooling; no core files are touched.
Usage:
  python3 -m venv .venv && . .venv/bin/activate
  pip install -e .
  python bench/loadtest.py                 # defaults, writes bench/results/
  python bench/loadtest.py --help          # all knobs
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

SOURCE_HEAD = "4090dd38"
TERMINAL_EVENTS = {"done", "failed", "cancelled"}
BENCH_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[rank]


def latency_stats(samples: list[float]) -> dict:
    """samples in seconds -> stats in milliseconds."""
    ms = [s * 1000 for s in samples]
    return {
        "count": len(ms),
        "min_ms": round(min(ms), 2) if ms else None,
        "mean_ms": round(statistics.fmean(ms), 2) if ms else None,
        "p50_ms": round(percentile(ms, 50), 2) if ms else None,
        "p95_ms": round(percentile(ms, 95), 2) if ms else None,
        "p99_ms": round(percentile(ms, 99), 2) if ms else None,
        "max_ms": round(max(ms), 2) if ms else None,
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def machine_info() -> dict:
    mem_mb = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_mb = round(int(line.split()[1]) / 1024)
                    break
    except OSError:
        pass
    import uvicorn

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "mem_total_mb": mem_mb,
        "httpx": httpx.__version__,
        "uvicorn": uvicorn.__version__,
    }


class ServerHandle:
    """A booted Meemee API server against a throwaway data dir."""

    def __init__(self, env_overrides: dict[str, str], name: str):
        self.name = name
        self.data_dir = Path(tempfile.mkdtemp(prefix=f"meemee-bench-{name}-"))
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.admin_token = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "MEEMEE_DATA_DIR": str(self.data_dir),
            "MEEMEE_API_TOKEN": self.admin_token,
            "MEEMEE_LOG_LEVEL": "warning",
            # No model is needed for queue/SSE/quota/rate-limit work; readiness
            # is intentionally not part of these measurements.
            "MEEMEE_MODEL_BASE_URL": "http://127.0.0.1:9/v1",
            **env_overrides,
        }
        self.log_path = self.data_dir / "server.log"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "meemee.api:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            env=env, stdout=self.log_path.open("w"), stderr=subprocess.STDOUT,
        )
        self._wait_healthy()

    def _wait_healthy(self, timeout: float = 30.0) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} server exited early; see {self.log_path}")
            try:
                resp = httpx.get(f"{self.base}/health", timeout=1.0)
                if resp.status_code == 200:
                    return
            except httpx.TransportError:
                time.sleep(0.1)
        raise RuntimeError(f"{self.name} server did not become healthy in {timeout}s")

    def client(self, token: str | None = None, **kwargs) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=self.base, headers=headers, **kwargs)

    def admin(self, **kwargs) -> httpx.Client:
        return self.client(self.admin_token, **kwargs)

    def mint_token(self, name: str, scopes: list[str]) -> tuple[str, str]:
        with self.admin(timeout=10) as client:
            resp = client.post("/v1/tokens", json={"name": name, "scopes": scopes})
            resp.raise_for_status()
            body = resp.json()
        return body["id"], body["token"]

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()


# --------------------------------------------------------------------------- #
# scenario 1: job create throughput
# --------------------------------------------------------------------------- #

def scenario_job_create_throughput(server: ServerHandle, total: int, concurrency: int) -> dict:
    token_id, token = server.mint_token("bench-throughput", ["jobs:write", "jobs:read"])
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    lock = threading.Lock()
    quota_remaining: list[int] = []
    error_samples: list[str] = []

    def create_one(index: int) -> None:
        started = time.perf_counter()
        with server.client(token, timeout=30) as client:
            resp = client.post("/v1/jobs", json={"goal": f"bench throughput job {index}"})
        elapsed = time.perf_counter() - started
        with lock:
            latencies.append(elapsed)
            statuses[str(resp.status_code)] = statuses.get(str(resp.status_code), 0) + 1
            if resp.status_code == 200:
                quota_remaining.append(resp.json()["quota"]["remaining"])
            elif len(error_samples) < 3:
                error_samples.append(resp.text[:200])

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(create_one, range(total)))
    wall = time.perf_counter() - wall_start

    ok = statuses.get("200", 0)
    return {
        "params": {"total_jobs": total, "concurrency": concurrency},
        "wall_s": round(wall, 3),
        "throughput_jobs_per_s": round(ok / wall, 1) if wall else None,
        "status_counts": statuses,
        "latency": latency_stats(latencies),
        "non_200_samples": error_samples,
        "checks": {
            "all_created": ok == total,
            "no_server_errors": not any(s.startswith("5") for s in statuses),
            "quota_accounting_consistent": not quota_remaining or (
                sorted(quota_remaining)
                == list(range(min(quota_remaining), max(quota_remaining) + 1))
            ),
        },
        "notes": f"jobs remain queued (no worker); principal {token_id}",
    }


# --------------------------------------------------------------------------- #
# scenario 2: SSE fan-out
# --------------------------------------------------------------------------- #

def scenario_sse_fanout(server: ServerHandle, subscribers: int) -> dict:
    _, token = server.mint_token("bench-sse", ["jobs:write", "jobs:read"])
    with server.client(token, timeout=10) as client:
        job_id = client.post("/v1/jobs", json={"goal": "bench sse fanout job"}).json()["id"]

    t0 = time.perf_counter()
    results: list[dict] = [{"events": [], "status": None, "error": None}
                           for _ in range(subscribers)]

    def subscribe(index: int) -> None:
        result = results[index]
        try:
            timeout = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)
            with server.client(token, timeout=timeout) as client:
                with client.stream("GET", f"/v1/jobs/{job_id}/stream") as resp:
                    result["status"] = resp.status_code
                    for line in resp.iter_lines():
                        if line.startswith("event:"):
                            kind = line.split(":", 1)[1].strip()
                            result["events"].append(
                                {"kind": kind, "t": round(time.perf_counter() - t0, 4)})
                            if kind in TERMINAL_EVENTS:
                                return
        except Exception as exc:  # noqa: BLE001 - record, do not crash the fan-out
            result["error"] = repr(exc)

    threads = [threading.Thread(target=subscribe, args=(i,), daemon=True)
               for i in range(subscribers)]
    for thread in threads:
        thread.start()

    # Wait until every subscriber has replayed the initial 'queued' event,
    # which proves its stream is established.
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        if all(any(e["kind"] == "queued" for e in r["events"]) for r in results):
            break
        time.sleep(0.05)
    connected = sum(1 for r in results if any(e["kind"] == "queued" for e in r["events"]))

    with server.client(token, timeout=10) as client:
        resp = client.delete(f"/v1/jobs/{job_id}")
        cancel_status = resp.status_code
    cancel_done = time.perf_counter() - t0

    for thread in threads:
        thread.join(timeout=45)

    terminal_latencies = []
    kinds_seen: dict[str, int] = {}
    http_statuses: dict[str, int] = {}
    for result in results:
        code = str(result["status"])
        http_statuses[code] = http_statuses.get(code, 0) + 1
        terminal = next((e for e in result["events"] if e["kind"] in TERMINAL_EVENTS), None)
        if terminal:
            terminal_latencies.append(terminal["t"] - cancel_done)
        for event in result["events"]:
            kinds_seen[event["kind"]] = kinds_seen.get(event["kind"], 0) + 1

    errors = [r["error"] for r in results if r["error"]]
    received_terminal = len(terminal_latencies)
    return {
        "params": {"subscribers": subscribers, "job_id": job_id},
        "subscribers_connected": connected,
        "subscribers_received_terminal": received_terminal,
        "subscriber_http_statuses": http_statuses,
        "cancel_http_status": cancel_status,
        "event_kind_totals": kinds_seen,
        "terminal_latency_after_cancel": latency_stats(terminal_latencies),
        "fanout_gap_ms": round((max(terminal_latencies) - min(terminal_latencies)) * 1000, 2)
        if terminal_latencies else None,
        "errors": errors,
        "checks": {
            "all_connected": connected == subscribers,
            "all_received_terminal": received_terminal == subscribers,
            "no_errors": not errors,
        },
    }


# --------------------------------------------------------------------------- #
# scenario 3: concurrent principals
# --------------------------------------------------------------------------- #

def scenario_concurrent_principals(server: ServerHandle, principals: int, jobs_each: int) -> dict:
    tokens = [server.mint_token(f"bench-principal-{i}", ["jobs:write", "jobs:read"])
              for i in range(principals)]
    created: dict[str, int] = {tid: 0 for tid, _ in tokens}
    statuses: dict[str, int] = {}
    lock = threading.Lock()
    error_samples: list[str] = []

    def work(principal_index: int, job_index: int) -> None:
        token_id, token = tokens[principal_index]
        with server.client(token, timeout=30) as client:
            resp = client.post(
                "/v1/jobs", json={"goal": f"bench principal {principal_index} job {job_index}"})
        with lock:
            statuses[str(resp.status_code)] = statuses.get(str(resp.status_code), 0) + 1
            if resp.status_code == 200:
                created[token_id] += 1
            elif len(error_samples) < 3:
                error_samples.append(resp.text[:200])

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=principals * 4) as pool:
        list(pool.map(lambda pair: work(*pair),
                      [(p, j) for p in range(principals) for j in range(jobs_each)]))
    wall = time.perf_counter() - wall_start

    quota_reports = {}
    isolation_ok = True
    for token_id, token in tokens:
        with server.client(token, timeout=10) as client:
            quota = client.get("/v1/quota").json()
        quota_reports[token_id] = quota
        # Isolation property: each principal's recorded usage equals exactly the
        # jobs that principal successfully created - no leakage across principals.
        if quota["used"] != created[token_id]:
            isolation_ok = False

    total = principals * jobs_each
    ok = statuses.get("200", 0)
    return {
        "params": {"principals": principals, "jobs_each": jobs_each},
        "wall_s": round(wall, 3),
        "throughput_jobs_per_s": round(ok / wall, 1) if wall else None,
        "status_counts": statuses,
        "per_principal_quota_used": {tid: q["used"] for tid, q in quota_reports.items()},
        "non_200_samples": error_samples,
        "checks": {
            "all_created": ok == total,
            "no_server_errors": not any(s.startswith("5") for s in statuses),
            "per_principal_quota_isolated": isolation_ok,
        },
    }


# --------------------------------------------------------------------------- #
# scenario 4: quota enforcement under load
# --------------------------------------------------------------------------- #

def scenario_quota_enforcement(quota_limit: int, attempts: int, concurrency: int) -> dict:
    with ServerHandle({
        "MEEMEE_DEFAULT_DAILY_JOBS": str(quota_limit),
        "MEEMEE_RATE_LIMIT_REQUESTS": "1000000",
    }, "quota") as server:
        token_id, token = server.mint_token("bench-quota", ["jobs:write", "jobs:read"])
        statuses: dict[str, int] = {}
        retry_afters: list[str] = []
        details: dict[str, int] = {}
        lock = threading.Lock()

        def create_one(index: int) -> None:
            with server.client(token, timeout=30) as client:
                resp = client.post("/v1/jobs", json={"goal": f"bench quota probe {index}"})
            with lock:
                statuses[str(resp.status_code)] = statuses.get(str(resp.status_code), 0) + 1
                if resp.status_code == 429:
                    retry_afters.append(resp.headers.get("retry-after", ""))
                    detail = resp.json().get("detail", "")
                    details[detail] = details.get(detail, 0) + 1

        wall_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(create_one, range(attempts)))
        wall = time.perf_counter() - wall_start

        with server.client(token, timeout=10) as client:
            quota_after = client.get("/v1/quota").json()

        # Admin override raises the ceiling; one more job must succeed.
        with server.admin(timeout=10) as client:
            override = client.put(f"/v1/quota/{token_id}",
                                  json={"daily_jobs": quota_limit + 10}).json()
        with server.client(token, timeout=10) as client:
            after_override = client.post("/v1/jobs", json={"goal": "bench quota after override"})

    return {
        "params": {"quota_limit": quota_limit, "attempts": attempts,
                   "concurrency": concurrency},
        "wall_s": round(wall, 3),
        "status_counts": statuses,
        "created_exactly_limit": statuses.get("200", 0) == quota_limit,
        "rejected": statuses.get("429", 0),
        "reject_retry_after_values": sorted(set(retry_afters)),
        "reject_details": details,
        "quota_status_after": quota_after,
        "admin_override_status": override,
        "create_after_override": after_override.status_code,
        "checks": {
            "exactly_limit_created": statuses.get("200", 0) == quota_limit,
            "overflow_rejected_429": statuses.get("429", 0) == attempts - quota_limit,
            "retry_after_is_daily": set(retry_afters) == {"86400"},
            "usage_stops_at_limit": quota_after["used"] == quota_limit
            and quota_after["remaining"] == 0,
            "admin_override_restores": after_override.status_code == 200,
        },
    }


# --------------------------------------------------------------------------- #
# scenario 5: fixed-window rate limit under load
# --------------------------------------------------------------------------- #

def scenario_rate_limit(limit: int, window_seconds: int, attempts: int, concurrency: int) -> dict:
    with ServerHandle({
        "MEEMEE_RATE_LIMIT_REQUESTS": str(limit),
        "MEEMEE_RATE_LIMIT_WINDOW_SECONDS": str(window_seconds),
        "MEEMEE_DEFAULT_DAILY_JOBS": "1000000",
    }, "ratelimit") as server:
        _, token = server.mint_token("bench-ratelimit", ["jobs:read", "jobs:write"])
        statuses: dict[str, int] = {}
        retry_afters: list[int] = []
        limit_headers: list[str] = []
        remaining_on_first: list[int] = []
        lock = threading.Lock()

        def probe(index: int) -> None:
            with server.client(token, timeout=30) as client:
                resp = client.get("/v1/quota")
            with lock:
                statuses[str(resp.status_code)] = statuses.get(str(resp.status_code), 0) + 1
                limit_headers.append(resp.headers.get("ratelimit-limit", ""))
                if resp.status_code == 429:
                    retry_afters.append(int(resp.headers.get("retry-after", "-1")))
                elif resp.status_code == 200 and index == 0:
                    remaining_on_first.append(
                        int(resp.headers.get("ratelimit-remaining", "-1")))

        wall_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(probe, range(attempts)))
        wall = time.perf_counter() - wall_start

    return {
        "params": {"limit": limit, "window_seconds": window_seconds,
                   "attempts": attempts, "concurrency": concurrency},
        "wall_s": round(wall, 3),
        "status_counts": statuses,
        "ratelimit_limit_header_values": sorted(set(limit_headers)),
        "retry_after_range_s": [min(retry_afters), max(retry_afters)] if retry_afters else None,
        "checks": {
            "exactly_limit_allowed": statuses.get("200", 0) == limit,
            "overflow_rejected_429": statuses.get("429", 0) == attempts - limit,
            "retry_after_within_window": bool(retry_afters)
            and 1 <= min(retry_afters) and max(retry_afters) <= window_seconds,
            "limit_header_matches": set(limit_headers) == {str(limit)},
        },
    }


# --------------------------------------------------------------------------- #
# scenario 6: retention sweep timing (operator CLI on seeded data)
# --------------------------------------------------------------------------- #

def seed_retention_data(data_dir: Path, old_jobs: int, events_per_job: int,
                        queued_jobs: int, old_memories: int, expired_idem: int) -> dict:
    """Seed a data dir with old terminal data plus protected fresh rows."""
    from meemee.idempotency import IdempotencyStore
    from meemee.memory import MemoryStore
    from meemee.rate_limit import SQLiteRateLimiter

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=60)).isoformat()  # beyond the 30-day job window
    old_memory = (now - timedelta(days=120)).isoformat()  # beyond the 90-day memory window
    fresh = now.isoformat()

    jobs_db = sqlite3.connect(data_dir / "jobs.sqlite3")
    jobs_db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, run_at TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
            max_attempts INTEGER NOT NULL DEFAULT 3, result TEXT, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS job_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, sequence);
    """)
    jobs_db.executemany(
        "INSERT INTO jobs(id,goal,run_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        [(f"old-{i}", "seeded old job", old, "done", old, old) for i in range(old_jobs)]
        + [(f"live-{i}", "seeded queued job", fresh, "queued", fresh, fresh)
           for i in range(queued_jobs)],
    )
    jobs_db.executemany(
        "INSERT INTO job_events(job_id,kind,payload,created_at) VALUES(?,?,?,?)",
        [(f"old-{i}", kind, "{}", old)
         for i in range(old_jobs) for kind in ("queued", "running", "done")][:old_jobs * events_per_job]
        + [(f"live-{i}", "queued", "{}", fresh) for i in range(queued_jobs)],
    )
    jobs_db.commit()
    jobs_db.close()

    MemoryStore(data_dir / "meemee.sqlite3")
    mem_db = sqlite3.connect(data_dir / "meemee.sqlite3")
    mem_db.executemany(
        "INSERT INTO memories(run_id,kind,content,created_at) VALUES(?,?,?,?)",
        [("bench", "note", "old memory", old_memory) for _ in range(old_memories)]
        + [("bench", "note", "fresh memory", fresh) for _ in range(50)],
    )
    mem_db.commit()
    mem_db.close()

    IdempotencyStore(data_dir / "idempotency.sqlite3")
    idem_db = sqlite3.connect(data_dir / "idempotency.sqlite3")
    idem_db.executemany(
        "INSERT INTO idempotency VALUES(?,?,?,?,?,?,?,?)",
        [("bench", "/v1/jobs", f"expired-{i}", "h", "{}", 200, old, old)
         for i in range(expired_idem)]
        + [("bench", "/v1/jobs", f"live-{i}", "h", "{}", 200, fresh,
            (now + timedelta(days=1)).isoformat()) for i in range(50)],
    )
    idem_db.commit()
    idem_db.close()

    SQLiteRateLimiter(data_dir / "rate-limits.sqlite3", 60, 60)
    rl_db = sqlite3.connect(data_dir / "rate-limits.sqlite3")
    old_window = int((now - timedelta(days=2)).timestamp())
    rl_db.executemany(
        "INSERT INTO rate_limits VALUES(?,?,?)",
        [(f"identity-{i}", old_window - i * 60, 5) for i in range(500)],
    )
    rl_db.commit()
    rl_db.close()

    return {
        "old_terminal_jobs": old_jobs, "old_job_events": old_jobs * events_per_job,
        "protected_queued_jobs": queued_jobs, "old_memories": old_memories,
        "fresh_memories": 50, "expired_idempotency": expired_idem,
        "live_idempotency": 50, "stale_rate_windows": 500,
    }


def scenario_retention_sweep(old_jobs: int, events_per_job: int) -> dict:
    data_dir = Path(tempfile.mkdtemp(prefix="meemee-bench-retention-"))
    seeded = seed_retention_data(
        data_dir, old_jobs=old_jobs, events_per_job=events_per_job,
        queued_jobs=500, old_memories=5000, expired_idem=2000)

    env = {**os.environ, "MEEMEE_DATA_DIR": str(data_dir), "MEEMEE_LOG_LEVEL": "warning"}
    started = time.perf_counter()
    # meemee.cli has no __main__ guard, so invoke the typer app explicitly.
    proc = subprocess.run(
        [sys.executable, "-c", "from meemee.cli import app; app()", "retention-run"],
        env=env, capture_output=True, text=True, timeout=300,
    )
    wall = time.perf_counter() - started
    report = json.loads(proc.stdout) if proc.returncode == 0 else {"error": proc.stderr[-500:]}

    jobs_db = sqlite3.connect(data_dir / "jobs.sqlite3")
    remaining = {
        "jobs": jobs_db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "job_events": jobs_db.execute("SELECT COUNT(*) FROM job_events").fetchone()[0],
        "queued_jobs": jobs_db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0],
    }
    jobs_db.close()
    mem_db = sqlite3.connect(data_dir / "meemee.sqlite3")
    remaining["memories"] = mem_db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    mem_db.close()

    total_rows = (seeded["old_terminal_jobs"] + seeded["old_job_events"]
                  + seeded["old_memories"] + seeded["expired_idempotency"]
                  + seeded["stale_rate_windows"])
    return {
        "params": {"old_jobs": old_jobs, "events_per_job": events_per_job},
        "seeded": seeded,
        "wall_s": round(wall, 3),
        "cli_report": report,
        "remaining_after_sweep": remaining,
        "sweep_rows_per_s": round(total_rows / wall, 0) if wall else None,
        "checks": {
            "old_jobs_deleted": report.get("terminal_jobs") == old_jobs,
            "old_events_deleted": report.get("job_events") == old_jobs * events_per_job,
            "queued_jobs_protected": remaining["queued_jobs"] == 500,
            "old_memories_deleted": report.get("memories") == 5000,
            "expired_idempotency_deleted": report.get("idempotency") == 2000,
            "stale_windows_deleted": report.get("rate_limits") == 500,
            "audit_untouched": report.get("audit_entries") == 0,
        },
    }


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", type=int, default=600, help="throughput job count")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--sse-subscribers", type=int, default=20)
    parser.add_argument("--principals", type=int, default=8)
    parser.add_argument("--jobs-per-principal", type=int, default=40)
    parser.add_argument("--quota-limit", type=int, default=25)
    parser.add_argument("--rate-limit", type=int, default=30)
    parser.add_argument("--rate-window", type=int, default=60)
    parser.add_argument("--retention-jobs", type=int, default=10000)
    parser.add_argument("--events-per-job", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None, help="result JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = utcnow()
    t0 = time.perf_counter()
    results: dict = {
        "meta": {
            "harness": "bench/loadtest.py",
            "source_tree_head": SOURCE_HEAD,
            "started_at": started,
            "machine": machine_info(),
        },
        "scenarios": {},
    }

    with ServerHandle({
        "MEEMEE_RATE_LIMIT_REQUESTS": "1000000",
        "MEEMEE_DEFAULT_DAILY_JOBS": "1000000",
    }, "perf") as perf:
        print(f"[perf server] {perf.base} data={perf.data_dir}")
        print(f"[1/6] job create throughput: {args.jobs} jobs x{args.concurrency}")
        results["scenarios"]["job_create_throughput"] = scenario_job_create_throughput(
            perf, args.jobs, args.concurrency)
        print(f"[2/6] SSE fan-out: {args.sse_subscribers} subscribers")
        results["scenarios"]["sse_fanout"] = scenario_sse_fanout(perf, args.sse_subscribers)
        print(f"[3/6] concurrent principals: {args.principals} x {args.jobs_per_principal}")
        results["scenarios"]["concurrent_principals"] = scenario_concurrent_principals(
            perf, args.principals, args.jobs_per_principal)

    print(f"[4/6] quota enforcement: limit {args.quota_limit}")
    results["scenarios"]["quota_enforcement"] = scenario_quota_enforcement(
        args.quota_limit, attempts=args.quota_limit * 2 + 10, concurrency=8)
    print(f"[5/6] rate limit: {args.rate_limit}/{args.rate_window}s")
    results["scenarios"]["rate_limit"] = scenario_rate_limit(
        args.rate_limit, args.rate_window, attempts=args.rate_limit + 15, concurrency=4)
    print(f"[6/6] retention sweep: {args.retention_jobs} old jobs")
    results["scenarios"]["retention_sweep"] = scenario_retention_sweep(
        args.retention_jobs, args.events_per_job)

    results["meta"]["duration_s"] = round(time.perf_counter() - t0, 2)
    results["meta"]["finished_at"] = utcnow()
    all_checks = {
        f"{name}.{check}": passed
        for name, scenario in results["scenarios"].items()
        for check, passed in scenario.get("checks", {}).items()
    }
    results["checks"] = all_checks
    results["all_checks_passed"] = all(all_checks.values())

    out = args.out
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = BENCH_DIR / "results" / f"run-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nresults: {out}")
    print(f"all checks passed: {results['all_checks_passed']}")
    for name, passed in all_checks.items():
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    return 0 if results["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

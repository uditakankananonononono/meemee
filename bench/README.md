# bench/ - Meemee load & performance harness

Additive benchmarking tooling. Nothing outside `bench/` is touched.

`loadtest.py` boots a real Meemee API server (uvicorn subprocess, throwaway
`MEEMEE_DATA_DIR`, bootstrap admin token, model endpoint pointed nowhere - no
model is needed for queue/SSE/quota/rate-limit/retention work) and drives it
over HTTP with `httpx`. Each scenario prints progress, writes one
machine-readable JSON document to `bench/results/run-<utc>.json`, and the
process exits 0 only if every behavioral check passes.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
python bench/loadtest.py                 # defaults, ~35s wall on a 2-core box
python bench/loadtest.py --help          # every knob: job counts, concurrency,
                                         # subscribers, principals, limits, seed sizes
python bench/loadtest.py --jobs 2000 --concurrency 48 --out /tmp/big.json
```

Checks are printed as PASS/FAIL lines and mirrored under `checks` /
`all_checks_passed` in the JSON, so a CI job can gate on the exit code.

## Scenarios

| # | Scenario | What it measures | Server profile |
|---|----------|------------------|----------------|
| 1 | `job_create_throughput` | Concurrent `POST /v1/jobs`: wall time, jobs/s, min/mean/p50/p95/p99/max latency, status mix, quota-accounting consistency | rate limit and daily quota raised to 1e6 so they do not interfere |
| 2 | `sse_fanout` | K concurrent `GET /v1/jobs/{id}/stream` subscribers on one queued job; the job is then cancelled via `DELETE`; per-subscriber terminal-event latency after the cancel returns, fan-out gap between first and last delivery | same perf server |
| 3 | `concurrent_principals` | P independently scoped tokens creating jobs concurrently; per-principal quota usage must equal exactly that principal's own successful creations | same perf server |
| 4 | `quota_enforcement` | `MEEMEE_DEFAULT_DAILY_JOBS=25`: exactly 25 creates succeed under a concurrent storm, overflow is HTTP 429 with `Retry-After: 86400`, usage stops at the limit, and an admin `PUT /v1/quota/{principal}` override restores capacity | fresh server, low quota |
| 5 | `rate_limit` | `MEEMEE_RATE_LIMIT_REQUESTS=30` per 60s window: exactly 30 requests pass, overflow is HTTP 429 with `Retry-After` inside the window, `RateLimit-*` headers present and correct | fresh server, low window limit |
| 6 | `retention_sweep` | Seeds 10k old terminal jobs + 30k events, 5k old memories, 2k expired idempotency keys, 500 stale rate windows, plus protected live rows; times the operator-facing `meemee retention-run` CLI end to end and verifies deletions and survivors | no server; CLI on a seeded data dir |

Every scenario also asserts *behavioral* correctness (exact 200/429 splits,
header values, deletion counts, protected-row survival), not just timing.

## Measured results (actual runs on the machine this was written on)

Environment (recorded in every result file under `meta.machine`):
Linux 6.1 x86-64, 2 CPU cores, 1983 MB RAM, Python 3.10.12, uvicorn 0.53.0,
httpx 0.28.1, source tree at HEAD `4090dd38`. Canonical run:
`results/run-20260921T151321Z.json` (all defaults; 34.1s total).

| Scenario | Result |
|----------|--------|
| Job create throughput (600 jobs, concurrency 24) | **35.1 jobs/s**, p50 679ms, p95 1011ms, p99 1071ms, max 1192ms, 600/600 HTTP 200 |
| SSE fan-out (20 subscribers) | 20/20 received the terminal `cancelled` event; p50 delivery **417ms** after the cancel call returned; first-to-last subscriber gap 427ms (stream poll interval is 0.5s, so this is one poll cycle) |
| Concurrent principals (8 x 40 jobs) | 35.2 jobs/s aggregate; per-principal quota usage matched each principal's own creations exactly; **7/320 requests (2.2%) returned HTTP 500** - see known finding below |
| Quota enforcement (limit 25, 60 attempts, concurrency 8) | exactly 25 x HTTP 200, 35 x HTTP 429, all 429s `Retry-After: 86400`, usage frozen at 25/0 remaining; admin override to 35 let the next create through (200) |
| Rate limit (30 per 60s, 45 attempts, concurrency 4) | exactly 30 x HTTP 200, 15 x HTTP 429, `Retry-After: 40`, `RateLimit-Limit: 30` on every response |
| Retention sweep (47.5k seeded stale rows) | CLI completed in **0.354s** (~134k rows/s); deleted 10000 jobs, 30000 events, 5000 memories, 2000 idempotency keys, 500 rate windows; 500 queued jobs and 50 fresh memories survived; audit untouched |

Repeatability: four full default runs on this machine (the canonical run plus
`results/run{1,2,3}.json`) agree closely - throughput 33.4-35.2 jobs/s,
retention sweep 0.354-0.421s, SSE terminal p50 398-417ms. Expect different
absolute numbers on different hardware; the behavioral checks should hold
anywhere.

## Known finding surfaced by this harness (not fixed here)

`TokenStore.authenticate` (`meemee/auth.py`) writes `last_used_at` inside
`with self.db:` on one shared `sqlite3` connection with no lock. FastAPI runs
the auth dependency concurrently in its threadpool, so under concurrent
authenticated load two interleaved transactions race and one fails with
`sqlite3.OperationalError: cannot commit - no transaction is active`, surfacing
as an HTTP 500 with the safe generic body.

Observed: 2-10 failures per 600 requests (~0.3-1.7%) in scenario 1 and 1-7 per
320 (~0.3-2.2%) in scenario 3 across the four runs; server-log tracebacks name
`meemee/auth.py:62 with self.db:` as the raise site. The failing check is
`no_server_errors`. Quota accounting stays correct for these failures because
the 500 is raised at auth, before quota consumption or enqueue.

This overlay is restricted to additive `bench/` files, so the fix (a threading
lock around the authenticate transaction, as `JobStore`/`QuotaStore` already
have) is reported, not applied. `meemee/audit.py`'s `AuditLog.append` has the
same unlocked shared-connection shape and is worth auditing with it.

## Layout

```
bench/
  loadtest.py          the harness (stdlib + httpx only)
  README.md            this file
  results/             real JSON output from runs on the authoring machine
```

Result JSONs are run artifacts; regenerate them freely. Temp data dirs land in
`/tmp/meemee-bench-*` and can be deleted after a run.

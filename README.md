# Meemee

Meemee is Udita's private, local-first agent runtime. It turns a goal into an inspectable plan, gives a model a bounded set of real tools, records every result, and stops honestly when it finishes or cannot continue. This is a working v1, not a claim to be finished general intelligence.

## Verified in v0.3.0 (19)

1. Strict JSON agent loop with a configurable step limit.
2. OpenAI-compatible model client for Ollama, vLLM, llama.cpp, or hosted endpoints.
3. Typed tool registry with JSON Schema and validated arguments.
4. Read/write/execute risk classes and a write approval gate.
5. Live GitHub repository search with filters and quality ranking based on popularity, recency, maintenance, and archive status.
6. Workspace-confined file read/write tools with traversal protection.
7. Deterministic task plans with dependency validation.
8. Durable SQLite event memory with WAL, FTS5 retrieval, and run provenance.
9. CLI for runs, GitHub scouting, and API serving.
10. FastAPI health/run endpoints, Docker image, and automated tests.
11. Allowlisted direct-argv command execution with no shell interpolation, timeout, output cap, and explicit approval.
12. Git status/diff/log inspection plus approval-gated local commits of explicit paths; no implicit push.
13. Durable scheduled job queue with atomic multi-process claims, retry limits, and result/error records.
14. Long-running worker command and job create/status API.
15. Optional constant-time bearer-token authentication for run and job endpoints.
16. Concurrent sub-agent delegation with isolated agent instances, bounded concurrency, and ordered result provenance.
17. AES-256-GCM encrypted local secrets vault with per-record nonces and no secret-value listing.
18. Playwright Chromium navigation with title/text/status/screenshot capture, public-address enforcement, and workspace-safe screenshot paths.
19. Kubernetes API/worker deployments, persistent data claim, probes, resource limits, and restricted container security contexts.

## Thin (5)

- Plans are deterministic sentence decomposition, not a persisted editable plan state machine.
- Memory uses lexical FTS5, without embeddings or semantic reranking.
- Immediate API runs remain in-process; queued jobs support multiple workers but there is no event streaming or cancellation yet.
- API authentication is a single operator bearer token, not OIDC, users, roles, or scoped permissions.
- Browser navigation is real but limited to page reads and screenshots; interaction primitives and persistent profiles are not built yet.

## Missing, not claimed

Browser interaction/forms and persistent profiles, remote Git push/PR operations, permissions UI, OIDC/users/roles, rate limiting, semantic memory, event streaming, and job cancellation.

## Install

Python 3.10+ is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
pytest
```

The default model endpoint is Ollama's OpenAI-compatible API:

```bash
ollama pull qwen2.5-coder:14b
ollama serve
meemee run "Find actively maintained Python agent frameworks on GitHub and compare them"
```

Search GitHub directly without a model:

```bash
meemee scout "autonomous agent" --language Python --min-stars 100 --limit 10
```

Set `MEEMEE_GITHUB_TOKEN` to avoid GitHub's low anonymous search limit. Never commit it.

Start the API:

```bash
meemee serve --host 127.0.0.1 --port 8787
curl http://127.0.0.1:8787/health
curl -X POST http://127.0.0.1:8787/v1/runs -H 'content-type: application/json' \
  -d '{"goal":"Find the best current Python repository for local agents"}'
```

File writes are denied unless the caller opts in with `--approve-writes` or `approve_writes: true`. This coarse v1 switch is not a substitute for user-scoped production authorization.

## Architecture

- `agent.py`: bounded perceive-decide-act loop with evidence-preserving tool events.
- `tools/`: typed registry, GitHub scout, and safe workspace I/O.
- `memory.py`: SQLite event store and full-text retrieval.
- `planner.py`: dependency-checked initial planning.
- `llm.py`: OpenAI-compatible transport.
- `cli.py` and `api.py`: interfaces over the same runtime.

A model statement is never treated as proof that work happened. Tool returns are stored separately, side effects require approval, and paths stay inside the configured workspace.


## Advanced operation

Set `MEEMEE_API_TOKEN` in production-facing environments. Run one or more durable workers with `meemee worker`. Schedule work through `POST /v1/jobs` with an ISO 8601 `run_at`; workers atomically claim due jobs. Shell and local Git mutations are side effects and still require agent-run approval. Commands are direct argv calls, never `shell=True`, and only configured executables can run. Git commits name explicit paths and never push.

Browser setup: `pip install -e .[browser]` then `playwright install chromium`. Vault setup: run `meemee vault-key`, store the output as `MEEMEE_VAULT_KEY` outside the repo, and add secrets with `meemee vault-put NAME`. Kubernetes expects a separately managed `meemee-secrets` Secret; no plaintext secret manifest is committed.

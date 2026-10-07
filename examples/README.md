# Meemee SDK examples

Runnable, typed examples for `meemee-client`. Install the SDK first:

```bash
pip install -e ./sdk
```

Every example reads `MEEMEE_BASE_URL` (default `http://127.0.0.1:8787`) plus a
credential from the environment, and exits non-zero with a readable message on
API errors.

| Example | What it shows | Credential needed |
| --- | --- | --- |
| `quickstart.py` | Health check + synchronous `POST /v1/runs` | token with `runs:write` |
| `watch_job.py` | Enqueue a job, follow SSE progress to a terminal state | token with `jobs:write` + `jobs:read` |
| `stream_with_resume.py` | Persisting the SSE cursor to survive a process restart | token with `jobs:write` + `jobs:read` |
| `oidc_machine_client.py` | OIDC client-credentials auth with automatic refresh | IdP issuer + client id/secret |
| `admin_tokens_and_audit.py` | Minting a least-privilege token and walking the audit chain | admin token |

Run one:

```bash
export MEEMEE_API_TOKEN=mee_...
python examples/quickstart.py "Find three actively maintained Python agent frameworks"
```

## Monitor source-event walkthrough (no model)

`uv run --locked python examples/monitor_event_walkthrough.py` boots a real loopback API against temporary SQLite files, creates a price monitor, submits nonmatching/matching/repeated caller events, restarts the API and checks the retained trigger/count. No external source is contacted, model called, notification sent or existing account touched. The script uses an explicitly synthetic encryption key for disposable state only. The event feed is authenticated with `runs:write`; POST `/v1/monitors/evaluate` takes `source_id` and a finite JSON `event`, bounded to 100 fields and 32KiB. Submitted facts are not independently verified external truth. This shows one supplied-event monitoring path, not autonomous polling or alert delivery.

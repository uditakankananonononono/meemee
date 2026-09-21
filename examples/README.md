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

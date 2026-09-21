"""Admin workflow: mint a least-privilege token, then walk the audit chain.

Uses the bootstrap token (or any admin token) to create a scoped token for a
worker, then reads the tamper-evident audit log to confirm the mint was recorded.
The raw token is shown exactly once - store it in a secret manager immediately.

Usage:
    export MEEMEE_BASE_URL=http://127.0.0.1:8787
    export MEEMEE_ADMIN_TOKEN=...          # bootstrap or admin-scoped token
    python examples/admin_tokens_and_audit.py
"""
from __future__ import annotations

import os

from meemee_client import AuditEntry, MeemeeClient, MeemeeError


def main() -> int:
    base_url = os.environ.get("MEEMEE_BASE_URL", "http://127.0.0.1:8787")
    admin_token = os.environ["MEEMEE_ADMIN_TOKEN"]

    with MeemeeClient(base_url, auth=admin_token) as client:
        minted = client.tokens.create("nightly-worker", {"jobs:read", "jobs:write"})
        print(f"created token {minted.id}")
        print(f"raw token (shown once, store it now): {minted.token}")

        # The server verifies the whole hash chain before answering; a 500 here
        # means the chain failed verification and the log cannot be trusted.
        entries: list[AuditEntry] = list(client.audit.iter_entries())
        print(f"audit chain holds {len(entries)} verified entries")
        for entry in entries[-3:]:
            print(f"  #{entry.sequence} {entry.actor_id} {entry.action} {entry.resource} -> {entry.outcome}")

        # Revoke the demo token again so nothing least-privilege leaks into daily use.
        client.tokens.revoke(minted.id)
        print(f"revoked token {minted.id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeemeeError as exc:
        raise SystemExit(f"meemee error: {exc}")
    except KeyError as exc:
        raise SystemExit(f"missing environment variable: {exc}")

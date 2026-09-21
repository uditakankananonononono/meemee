"""Machine-to-machine access with OIDC client-credentials instead of an API token.

The Meemee server validates OIDC bearer tokens against the issuer's JWKS and
maps token roles to Meemee scopes (MEEMEE_OIDC_ROLE_SCOPES). This SDK acquires
and refreshes the token; the server never sees your client secret.

Usage:
    export MEEMEE_BASE_URL=https://meemee.example.com
    export OIDC_ISSUER=https://idp.example.com/realms/meemee
    export OIDC_CLIENT_ID=meemee-worker
    export OIDC_CLIENT_SECRET=...
    export OIDC_SCOPE=operator           # optional; mapped server-side to scopes
    python examples/oidc_machine_client.py
"""
from __future__ import annotations

import os

from meemee_client import MeemeeClient, MeemeeError, OIDCClientCredentialsAuth


def main() -> int:
    base_url = os.environ["MEEMEE_BASE_URL"]
    auth = OIDCClientCredentialsAuth(
        os.environ["OIDC_ISSUER"],
        client_id=os.environ["OIDC_CLIENT_ID"],
        client_secret=os.environ["OIDC_CLIENT_SECRET"],
        scope=os.environ.get("OIDC_SCOPE"),
    )

    with MeemeeClient(base_url, auth=auth) as client:
        created = client.jobs.create("Nightly GitHub scout for agent frameworks")
        print(f"job {created.id} enqueued via OIDC identity")
        job = client.jobs.wait(created.id, timeout=300, poll_interval=5)
        print(f"terminal state: {job.status.value}")
        if job.error:
            print(f"error: {job.error}")
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeemeeError as exc:
        raise SystemExit(f"meemee error: {exc}")
    except KeyError as exc:
        raise SystemExit(f"missing environment variable: {exc}")

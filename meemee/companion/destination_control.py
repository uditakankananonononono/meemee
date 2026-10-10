"""Server-issued webhook endpoint-control proof, not message-send permission.

Nonce is private transport data, never returned to the requesting API caller.
Current SSRF validation has a documented DNS check/connect race; no redirects.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx

from ..webhooks import validate_webhook_url
from .checkin_leases import lease_clock


class DestinationVerificationError(ValueError):
    """Fixed public error; do not disclose nonce, endpoint response or owner state."""


def exact_webhook(destination):
    if (
        type(destination) is not str
        or not destination
        or len(destination) > 500
        or destination != destination.strip()
        or any(ord(c) < 33 or ord(c) == 127 for c in destination)
        or "\\" in destination
    ):
        raise DestinationVerificationError("destination verification refused")
    try:
        p = urlsplit(destination)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.fragment
            or p.port == 0
        ):
            raise DestinationVerificationError("destination verification refused")
    except ValueError:
        raise DestinationVerificationError("destination verification refused") from None
    return destination


def _public(row):
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in dict(row).items()
        if k != "nonce_sha256"
    }


class DestinationControlMixin:
    def create_destination_challenge(self, owner, destination, now=None):
        exact_webhook(destination)
        if type(owner) is not str or not owner or len(owner) > 240:
            raise DestinationVerificationError("destination verification refused")
        nonce = secrets.token_urlsafe(32)
        ident = uuid.uuid4().hex
        with self._lease_transaction() as c:
            user = c.execute(
                "SELECT user_id FROM companion_users WHERE user_id=?" + c.for_update, (owner,)
            ).fetchone()
            clock = lease_clock(now)
            expires = clock + timedelta(seconds=60)
            if user is None:
                raise DestinationVerificationError("destination verification refused")
            c.execute(
                "DELETE FROM companion_destination_challenges WHERE owner_id=? AND expires_at<=?",
                (owner, clock.isoformat()),
            )
            rows = c.execute(
                "SELECT created_at FROM companion_destination_challenges WHERE owner_id=?", (owner,)
            ).fetchall()
            if len(rows) >= 10 or any(
                lease_clock(datetime.fromisoformat(str(r["created_at"]))) + timedelta(seconds=2)
                > clock
                for r in rows
            ):
                raise DestinationVerificationError("destination verification refused")
            c.execute(
                "INSERT INTO companion_destination_challenges(id,owner_id,destination,nonce_sha256,created_at,expires_at,consumed_at) VALUES(?,?,?,?,?,?,NULL)",
                (
                    ident,
                    owner,
                    destination,
                    hashlib.sha256(nonce.encode()).hexdigest(),
                    clock.isoformat(),
                    expires.isoformat(),
                ),
            )
            row = c.execute(
                "SELECT * FROM companion_destination_challenges WHERE id=?", (ident,)
            ).fetchone()
        return _public(row), nonce

    def consume_destination_challenge(self, owner, ident, nonce, now=None):
        with self._lease_transaction() as c:
            user = c.execute(
                "SELECT user_id FROM companion_users WHERE user_id=?" + c.for_update, (owner,)
            ).fetchone()
            if user is None:
                raise DestinationVerificationError("destination verification refused")
            row = c.execute(
                "SELECT * FROM companion_destination_challenges WHERE id=? AND owner_id=?"
                + c.for_update,
                (ident, owner),
            ).fetchone()
            clock = lease_clock(now)
            if (
                row is None
                or row["consumed_at"] is not None
                or not lease_clock(datetime.fromisoformat(str(row["created_at"])))
                <= clock
                < lease_clock(datetime.fromisoformat(str(row["expires_at"])))
                or type(nonce) is not str
                or len(nonce) > 100
                or not hmac.compare_digest(
                    hashlib.sha256(nonce.encode()).hexdigest(), row["nonce_sha256"]
                )
            ):
                raise DestinationVerificationError("destination verification refused")
            c.execute(
                "UPDATE companion_destination_challenges SET consumed_at=? WHERE id=?",
                (clock.isoformat(), ident),
            )
            grant_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO companion_destination_grants(id,owner_id,channel,destination,issued_at,expires_at,revoked_at) VALUES(?,?,'webhook',?,?,?,NULL)",
                (
                    grant_id,
                    owner,
                    row["destination"],
                    clock.isoformat(),
                    (clock + timedelta(days=7)).isoformat(),
                ),
            )
            grant = c.execute(
                "SELECT * FROM companion_destination_grants WHERE id=?", (grant_id,)
            ).fetchone()
        return _public(grant)

    def _destination_grants_locked(self, c, owner, channel, destination):
        """Fetch proof rows with locks; do not evaluate a pre-lock clock."""
        if channel != "webhook":
            return []
        return c.execute(
            "SELECT * FROM companion_destination_grants WHERE owner_id=? AND channel='webhook' AND destination=? ORDER BY id"
            + c.for_share,
            (owner, destination),
        ).fetchall()

    def _destination_grants_valid(self, channel, rows, clock):
        if channel == "local":
            return True
        if channel != "webhook":
            return False
        return any(
            row["revoked_at"] is None
            and lease_clock(datetime.fromisoformat(str(row["issued_at"])))
            <= clock
            < lease_clock(datetime.fromisoformat(str(row["expires_at"])))
            for row in rows
        )

    def _destination_verified_locked(self, c, owner, channel, destination, now=None):
        rows = self._destination_grants_locked(c, owner, channel, destination)
        return self._destination_grants_valid(channel, rows, lease_clock(now))

    def destination_is_verified(self, owner, channel, destination, now=None):
        with self._lease_transaction() as c:
            return self._destination_verified_locked(c, owner, channel, destination, now)

    def revoke_destination(self, owner, grant_id):
        with self._lease_transaction() as c:
            row = c.execute(
                "SELECT id FROM companion_destination_grants WHERE id=? AND owner_id=?"
                + c.for_update,
                (grant_id, owner),
            ).fetchone()
            if row is None:
                return False
            c.execute(
                "UPDATE companion_destination_grants SET revoked_at=? WHERE id=?",
                (lease_clock().isoformat(), grant_id),
            )
        return True

    def list_destination_grants(self, owner):
        with self._lease_transaction() as c:
            return [
                _public(row)
                for row in c.execute(
                    "SELECT * FROM companion_destination_grants WHERE owner_id=? ORDER BY issued_at,id",
                    (owner,),
                ).fetchall()
            ]


async def verify_webhook_control(store, owner, destination, *, client=None):
    exact_webhook(destination)
    try:
        await asyncio.wait_for(asyncio.to_thread(validate_webhook_url, destination), 10)
        challenge, nonce = store.create_destination_challenge(owner, destination)
        body = {
            "kind": "meemee.destination_control",
            "challenge_id": challenge["id"],
            "nonce": nonce,
        }

        async def exchange(active):
            # Stream so hostile endpoint output cannot allocate unbounded response.
            async with active.stream(
                "POST", destination, json=body, follow_redirects=False, timeout=10
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise DestinationVerificationError("destination verification refused")
                raw = b""
                async for chunk in response.aiter_bytes(chunk_size=2049):
                    raw += chunk
                    if len(raw) > 2048:
                        raise DestinationVerificationError("destination verification refused")
                result = json.loads(raw)
                if (
                    type(result) is not dict
                    or set(result) != {"challenge_id", "nonce"}
                    or result["challenge_id"] != challenge["id"]
                    or type(result["nonce"]) is not str
                    or not hmac.compare_digest(result["nonce"], nonce)
                ):
                    raise DestinationVerificationError("destination verification refused")

        if client is None:
            async with httpx.AsyncClient(trust_env=False) as active:
                await asyncio.wait_for(exchange(active), 10)
        else:
            await asyncio.wait_for(exchange(client), 10)
        return store.consume_destination_challenge(owner, challenge["id"], nonce)
    except (ValueError, TypeError, httpx.HTTPError, OSError, asyncio.TimeoutError, RuntimeError):
        raise DestinationVerificationError("destination verification refused") from None

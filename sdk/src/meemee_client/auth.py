"""Authentication providers for the Meemee API.

The server accepts two bearer-credential families (v0.77.0):

1. Meemee API tokens (``mee_...``) minted via POST /v1/tokens, or the bootstrap
   token from MEEMEE_API_TOKEN - static bearer strings, handled by ``TokenAuth``.
2. OIDC access tokens issued by an external identity provider; the server
   validates them against the issuer's JWKS. ``OIDCClientCredentialsAuth``
   acquires and refreshes those tokens for machine-to-machine callers.

Interactive browser sign-in (Authorization Code + PKCE) is served by the
server itself at /auth/login and is out of scope for this SDK.
"""
from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

import httpx

from .errors import AuthenticationError, MeemeeError


@runtime_checkable
class AuthProvider(Protocol):
    """Anything that can produce an Authorization header value on demand."""

    def authorization_header(self) -> str:
        ...


class TokenAuth:
    """Static bearer token: a scoped Meemee API token or the bootstrap token."""

    def __init__(self, token: str) -> None:
        if not token or not token.strip():
            raise ValueError("token must be a non-empty string")
        self._token = token

    def authorization_header(self) -> str:
        return f"Bearer {self._token}"


class OIDCClientCredentialsAuth:
    """OAuth2 client-credentials tokens for Meemee's federated OIDC bearer auth.

    Discovers the token endpoint from the issuer's
    ``/.well-known/openid-configuration`` unless one is given explicitly, fetches
    access tokens with the client_credentials grant, caches them, and refreshes
    ``leeway_seconds`` before expiry.

    The caller is responsible for requesting scopes/roles that the server's
    role-to-scope mapping (MEEMEE_OIDC_ROLE_SCOPES) turns into Meemee scopes;
    a token with no mapped roles authenticates but is denied everywhere.
    """

    def __init__(
        self,
        issuer: str | None = None,
        *,
        token_endpoint: str | None = None,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        leeway_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not issuer and not token_endpoint:
            raise ValueError("provide either issuer (for discovery) or an explicit token_endpoint")
        if issuer is not None and not issuer.startswith(("https://", "http://")):
            raise ValueError("issuer must be an absolute http(s) URL")
        if not client_id:
            raise ValueError("client_id is required")
        if not client_secret:
            raise ValueError("client_secret is required")
        self._issuer = issuer.rstrip("/") if issuer else None
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._leeway = leeway_seconds
        self._http = http_client or httpx.Client(timeout=15.0)
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def authorization_header(self) -> str:
        return f"Bearer {self.access_token}"

    @property
    def access_token(self) -> str:
        with self._lock:
            if self._access_token is None or time.time() >= self._expires_at - self._leeway:
                self._fetch_token()
            assert self._access_token is not None  # set by _fetch_token or it raised
            return self._access_token

    def refresh(self) -> None:
        """Force a token fetch on the next use (e.g. after a 401)."""
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0

    def _discover_token_endpoint(self) -> str:
        if self._token_endpoint is not None:
            return self._token_endpoint
        assert self._issuer is not None
        url = f"{self._issuer}/.well-known/openid-configuration"
        try:
            response = self._http.get(url)
        except httpx.TransportError as exc:
            raise AuthenticationError(f"OIDC discovery request failed: {exc}", status_code=0) from exc
        if response.status_code != 200:
            raise AuthenticationError(
                f"OIDC discovery failed with status {response.status_code} for {url}",
                status_code=response.status_code,
            )
        try:
            endpoint = response.json()["token_endpoint"]
        except (ValueError, KeyError) as exc:
            raise AuthenticationError(
                f"OIDC discovery document at {url} has no token_endpoint", status_code=0
            ) from exc
        if not isinstance(endpoint, str) or not endpoint.startswith(("https://", "http://")):
            raise AuthenticationError("OIDC discovery returned an invalid token_endpoint", status_code=0)
        self._token_endpoint = endpoint
        return endpoint

    def _fetch_token(self) -> None:
        endpoint = self._discover_token_endpoint()
        data = {"grant_type": "client_credentials"}
        if self._scope:
            data["scope"] = self._scope
        try:
            # client_secret_basic, the RFC 6749 default authentication method.
            response = self._http.post(endpoint, data=data, auth=(self._client_id, self._client_secret))
        except httpx.TransportError as exc:
            raise AuthenticationError(f"OIDC token request failed: {exc}", status_code=0) from exc
        if response.status_code != 200:
            error, description = "", ""
            try:
                body = response.json()
                error = str(body.get("error", ""))
                description = str(body.get("error_description", ""))
            except ValueError:
                pass
            detail = " ".join(part for part in (error, description) if part) or response.text[:200]
            raise AuthenticationError(
                f"OIDC token request rejected with status {response.status_code}: {detail}",
                status_code=response.status_code,
            ) from None
        try:
            body = response.json()
        except ValueError as exc:
            raise AuthenticationError("OIDC token endpoint returned a non-JSON response", status_code=0) from exc
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthenticationError("OIDC token endpoint response has no access_token", status_code=0)
        token_type = str(body.get("token_type", "Bearer"))
        if token_type.lower() != "bearer":
            raise AuthenticationError(f"OIDC token endpoint returned unsupported token_type {token_type!r}", status_code=0)
        expires_in = body.get("expires_in", 300)
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError) as exc:
            raise AuthenticationError("OIDC token endpoint returned a non-numeric expires_in", status_code=0) from exc
        if lifetime <= 0:
            raise MeemeeError("OIDC token endpoint returned a non-positive expires_in")
        self._access_token = token
        self._expires_at = time.time() + lifetime

    def close(self) -> None:
        self._http.close()

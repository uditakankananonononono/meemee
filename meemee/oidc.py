from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from jwt import PyJWKClient

from .auth import Principal


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    audience: str
    jwks_url: str
    role_claim: str = "roles"
    role_scopes: str = "admin=admin;operator=runs:write,jobs:read,jobs:write;viewer=jobs:read"

    def __post_init__(self) -> None:
        issuer, jwks = urlparse(self.issuer), urlparse(self.jwks_url)
        if issuer.scheme != "https" or jwks.scheme != "https":
            raise ValueError("OIDC issuer and JWKS URL must use HTTPS")
        if jwks.hostname != issuer.hostname:
            raise ValueError("OIDC JWKS host must match issuer host")
        if not self.audience.strip():
            raise ValueError("OIDC audience cannot be empty")

    def mapping(self) -> dict[str, frozenset[str]]:
        result: dict[str, frozenset[str]] = {}
        for entry in self.role_scopes.split(";"):
            role, separator, scopes = entry.partition("=")
            if not separator or not role.strip():
                raise ValueError(f"invalid OIDC role mapping: {entry}")
            result[role.strip()] = frozenset(scope.strip() for scope in scopes.split(",") if scope.strip())
        return result


class OIDCValidator:
    """Validates signed OIDC access tokens using issuer JWKS, audience, expiry and role mapping."""

    def __init__(self, config: OIDCConfig, jwks_client: PyJWKClient | None = None):
        self.config = config
        self.jwks = jwks_client or PyJWKClient(config.jwks_url, cache_keys=True, lifespan=300)
        self.role_scopes = config.mapping()

    def authenticate(self, token: str) -> Principal | None:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
                return None
            signing_key = self.jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=[header["alg"]],
                audience=self.config.audience,
                issuer=self.config.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        roles = claims.get(self.config.role_claim, [])
        if isinstance(roles, str):
            roles = roles.split()
        if not isinstance(roles, list):
            return None
        scopes: set[str] = set()
        for role in roles:
            scopes.update(self.role_scopes.get(str(role), ()))
        direct = claims.get("scope", "")
        if isinstance(direct, str):
            scopes.update(direct.split())
        name = str(claims.get("preferred_username") or claims.get("email") or claims["sub"])
        return Principal(f"oidc:{claims['sub']}", name, frozenset(scopes))

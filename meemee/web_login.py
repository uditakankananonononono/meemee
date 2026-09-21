from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .auth import Principal
from .oidc import OIDCValidator


@dataclass(frozen=True)
class WebLoginConfig:
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str
    session_key: str

    def __post_init__(self):
        for url in (self.authorization_endpoint, self.token_endpoint, self.redirect_uri):
            if not url.startswith("https://"):
                raise ValueError("interactive OIDC endpoints and redirect URI require HTTPS")
        if len(self.session_key) < 32:
            raise ValueError("session key must contain at least 32 characters")


class WebLogin:
    """OIDC Authorization Code + PKCE flow with signed state and secure session cookies."""

    def __init__(self, config: WebLoginConfig, validator: OIDCValidator, client: httpx.AsyncClient | None = None):
        self.config, self.validator = config, validator
        self.client = client or httpx.AsyncClient(timeout=15)

    def encode(self, payload: dict, lifetime: timedelta) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode({**payload, "iat": now, "exp": now + lifetime}, self.config.session_key, algorithm="HS256")

    def decode(self, token: str, purpose: str) -> dict:
        try:
            return jwt.decode(token, self.config.session_key, algorithms=["HS256"], options={"require":["iat","exp"]}, audience=purpose)
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "invalid or expired login state") from exc

    def start(self) -> RedirectResponse:
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        cookie = self.encode({"aud":"login-state","state":state,"nonce":nonce,"verifier":verifier}, timedelta(minutes=10))
        query = urlencode({"response_type":"code","client_id":self.config.client_id,"redirect_uri":self.config.redirect_uri,"scope":"openid profile email","state":state,"nonce":nonce,"code_challenge":challenge,"code_challenge_method":"S256"})
        response = RedirectResponse(f"{self.config.authorization_endpoint}?{query}", 302)
        response.set_cookie("meemee_login", cookie, max_age=600, secure=True, httponly=True, samesite="lax", path="/auth")
        return response

    async def callback(self, request: Request, code: str, state: str) -> RedirectResponse:
        stored = self.decode(request.cookies.get("meemee_login", ""), "login-state")
        if not secrets.compare_digest(stored["state"], state):
            raise HTTPException(401, "OIDC state mismatch")
        response = await self.client.post(self.config.token_endpoint, data={"grant_type":"authorization_code","code":code,"redirect_uri":self.config.redirect_uri,"client_id":self.config.client_id,"client_secret":self.config.client_secret,"code_verifier":stored["verifier"]})
        response.raise_for_status()
        access = response.json().get("access_token")
        principal = self.validator.authenticate(access) if access else None
        if principal is None:
            raise HTTPException(401, "identity provider returned invalid access token")
        session = self.encode({"aud":"session","sub":principal.id,"name":principal.name,"scopes":sorted(principal.scopes)}, timedelta(hours=8))
        result = RedirectResponse("/", 302)
        result.delete_cookie("meemee_login", path="/auth")
        result.set_cookie("meemee_session", session, max_age=28800, secure=True, httponly=True, samesite="lax", path="/")
        return result

    def authenticate_session(self, token: str) -> Principal | None:
        try:
            payload = self.decode(token, "session")
            return Principal(payload["sub"], payload["name"], frozenset(payload["scopes"]))
        except HTTPException:
            return None

    @staticmethod
    def home(principal: Principal | None) -> HTMLResponse:
        if principal is None:
            return HTMLResponse('<!doctype html><html><body><h1>Meemee</h1><a href="/auth/login">Sign in</a></body></html>')
        name = principal.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return HTMLResponse(f'<!doctype html><html><body><h1>Meemee</h1><p>Signed in as {name}</p><form method="post" action="/auth/logout"><button>Sign out</button></form></body></html>')

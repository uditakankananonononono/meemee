"""Installed-app Google OAuth (authorization code + PKCE, loopback redirect) for GmailInbox.

The refresh token lives only in the vault. The access token is held in memory. Endpoints are fixed to
Google unless local_test=True (loopback stub). The granted scope must be exactly gmail.readonly: a
token that grants more or less is refused. The OAuth client id belongs to the account owner's own Google
Cloud project; nothing here ships a shared client. Not exercised against real Google in tests."""
import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

import httpx

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'


class OAuthError(Exception):
    pass


class GmailOAuth:
    def __init__(self, vault, client_id, refresh_ref='gmail-refresh', client_secret_ref=None,
                 token_endpoint=TOKEN, auth_endpoint=AUTH, client=None, local_test=False):
        for url in (token_endpoint, auth_endpoint):
            if url not in (TOKEN, AUTH):
                p = urlsplit(url)
                if not local_test or p.scheme != 'http' or p.hostname != '127.0.0.1':
                    raise ValueError('OAuth endpoints are fixed outside local testing')
        self.vault, self.client_id = vault, client_id
        self.refresh_ref, self.client_secret_ref = refresh_ref, client_secret_ref
        self.token_endpoint, self.auth_endpoint = token_endpoint, auth_endpoint
        self.client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)   # no env proxies, no redirects
        self._pending = {}
        self._lock = threading.Lock()      # one refresh/exchange at a time; no double refresh races
        self._access, self._expires = '', 0.0

    def begin(self, redirect_uri):
        p = urlsplit(redirect_uri)
        if p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost') or not p.port or p.query or p.fragment:
            raise ValueError('redirect must be a loopback http URL with a port')
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(24)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self._pending[state] = (verifier, redirect_uri, time.time() + 600)
        query = urlencode({'client_id': self.client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
                           'scope': SCOPE, 'state': state, 'code_challenge': challenge,
                           'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent'})
        return self.auth_endpoint + '?' + query, state

    def finish(self, state, code):
        with self._lock:
            entry = self._pending.pop(state, None)      # one use; unknown/replayed state is refused
        if not entry or time.time() > entry[2]:
            raise OAuthError('unknown or expired state')
        verifier, redirect_uri, _ = entry
        data = {'client_id': self.client_id, 'code': code, 'code_verifier': verifier,
                'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'}
        body = self._post(data)
        if not body.get('refresh_token'):
            raise OAuthError('no refresh token granted')
        self._remember(body)                          # validates scope/type BEFORE anything is persisted
        self.vault.put(self.refresh_ref, body['refresh_token'])

    def access_token(self):
        with self._lock:
            if self._access and time.time() < self._expires - 30:
                return self._access
            try:
                refresh = self.vault.get(self.refresh_ref)
            except KeyError:
                raise OAuthError('Gmail is not connected')
            self._remember(self._post({'client_id': self.client_id, 'refresh_token': refresh,
                                       'grant_type': 'refresh_token'}))
            return self._access

    def _post(self, data):
        if self.client_secret_ref:
            data = {**data, 'client_secret': self.vault.get(self.client_secret_ref)}
        response = self.client.post(self.token_endpoint, data=data)
        if response.status_code != 200:
            raise OAuthError('token endpoint refused the request')   # body never echoed (may hold secrets)
        return response.json()

    def _remember(self, body):
        if set(body.get('scope', '').split()) != {SCOPE}:
            raise OAuthError('granted scope is not exactly gmail.readonly')
        if str(body.get('token_type', '')).lower() != 'bearer' or not body.get('access_token'):
            raise OAuthError('unexpected token response')
        self._access = body['access_token']
        self._expires = time.time() + int(body.get('expires_in', 0))

from urllib.parse import parse_qs, urlparse

import pytest

from meemee.auth import Principal
from meemee.web_login import WebLogin, WebLoginConfig


class Validator:
    def authenticate(self, token): return Principal("oidc:u", "<Udita>", frozenset({"jobs:read"}))


def config():
    return WebLoginConfig("client", "secret", "https://login.example/authorize", "https://login.example/token", "https://app.example/auth/callback", "x" * 32)


def test_login_has_pkce_state_nonce_and_secure_cookie():
    login = WebLogin(config(), Validator())
    response = login.start()
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] and query["nonce"] and query["code_challenge"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie


def test_session_roundtrip_and_html_escaping():
    login = WebLogin(config(), Validator())
    encoded = login.encode({"aud":"session","sub":"u","name":"U","scopes":["jobs:read"]}, __import__('datetime').timedelta(minutes=1))
    assert login.authenticate_session(encoded).scopes == {"jobs:read"}
    assert "&lt;Udita&gt;" in login.home(Principal("u", "<Udita>", frozenset())).body.decode()


def test_web_config_requires_https_and_strong_key():
    with pytest.raises(ValueError, match="HTTPS"):
        WebLoginConfig("c","s","http://x/a","https://x/t","https://x/c","x"*32)
    with pytest.raises(ValueError, match="32"):
        WebLoginConfig("c","s","https://x/a","https://x/t","https://x/c","short")

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from meemee.oidc import OIDCConfig, OIDCValidator


class Key:
    def __init__(self, key): self.key = key


class FakeJWKS:
    def __init__(self, public): self.public = public
    def get_signing_key_from_jwt(self, token): return Key(self.public)


def token(private, **overrides):
    now = datetime.now(timezone.utc)
    claims = {"iss":"https://login.example.com/","aud":"meemee","sub":"user-1","iat":now,"exp":now+timedelta(minutes=5),"roles":["operator"],"preferred_username":"udita"}
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid":"test"})


def validator():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = OIDCConfig("https://login.example.com/", "meemee", "https://login.example.com/.well-known/jwks.json")
    return private, OIDCValidator(config, FakeJWKS(private.public_key()))


def test_oidc_validates_and_maps_roles():
    private, check = validator()
    principal = check.authenticate(token(private))
    assert principal and principal.id == "oidc:user-1"
    assert principal.name == "udita"
    assert principal.scopes == {"runs:write", "jobs:read", "jobs:write"}


def test_oidc_rejects_wrong_audience_and_expired():
    private, check = validator()
    assert check.authenticate(token(private, aud="other")) is None
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert check.authenticate(token(private, iat=past, exp=past+timedelta(minutes=1))) is None


def test_oidc_rejects_unsigned_algorithm():
    _, check = validator()
    unsigned = jwt.encode({"sub":"x"}, key="", algorithm="none")
    assert check.authenticate(unsigned) is None


def test_oidc_configuration_rejects_insecure_or_cross_host():
    with pytest.raises(ValueError, match="HTTPS"):
        OIDCConfig("http://login.example.com", "x", "https://login.example.com/keys")
    with pytest.raises(ValueError, match="match"):
        OIDCConfig("https://login.example.com", "x", "https://evil.example/keys")

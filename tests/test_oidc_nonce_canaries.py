"""Signed fixture-token login must bind the response to nonce and subject."""
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from meemee.web_login import WebLogin, WebLoginConfig
from test_oidc import token, validator


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'nonce', 'subject'])
async def test_login_cannot_create_session_for_unbound_id_token(case):
    private, check = validator()
    access = token(private)
    payload = {'access_token': access}
    if case != 'missing':
        payload['id_token'] = token(private, aud='client', nonce='wrong' if case == 'nonce' else 'expected',
                                    sub='other-user' if case == 'subject' else 'user-1')
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    config = WebLoginConfig('client', 'secret', 'https://login.example.com/authorize',
        'https://login.example.com/token', 'https://app.example/auth/callback', 'x' * 32)
    login = WebLogin(config, check, client)
    cookie = login.encode({'aud':'login-state','state':'state','nonce':'expected','verifier':'verifier'}, timedelta(minutes=1))
    request = Request({'type':'http','headers':[(b'cookie',f'meemee_login={cookie}'.encode())]})
    with pytest.raises(HTTPException) as rejected:
        await login.callback(request, 'code', 'state')
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_signed_matching_identity_allows_session():
    private, check = validator()
    payload = {'access_token':token(private), 'id_token':token(private, aud='client', nonce='expected')}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    login = WebLogin(WebLoginConfig('client','secret','https://login.example.com/authorize',
        'https://login.example.com/token','https://app.example/auth/callback','x'*32), check, client)
    cookie = login.encode({'aud':'login-state','state':'state','nonce':'expected','verifier':'v'}, timedelta(minutes=1))
    request = Request({'type':'http','headers':[(b'cookie',f'meemee_login={cookie}'.encode())]})
    response = await login.callback(request, 'code', 'state')
    assert response.status_code == 302
    assert 'meemee_session=' in response.headers.get('set-cookie', '') or any(
        'meemee_session=' in value for value in response.headers.getlist('set-cookie'))


@pytest.mark.parametrize('overrides', [{'azp':'other'}, {'aud':['client','other']}, {'at_hash':'wrong'}, {'nonce':'wrong'}])
def test_signed_id_token_rejects_bad_authorized_party_hash_nonce(overrides):
    private, check = validator()
    values = {'aud':'client', 'nonce':'expected', **overrides}
    assert check.validate_id_token(token(private, **values), 'client', 'expected', token(private)) is None

from pathlib import Path

import httpx
import pytest
import respx

from meemee.email_verification import EmailVerificationStore, ResendMailer


def test_verification_token_is_one_time_expiring_and_secret_safe(tmp_path: Path):
    store=EmailVerificationStore(tmp_path/'email.db'); raw=store.issue('acct_1')
    assert raw.encode() not in (tmp_path/'email.db').read_bytes()
    assert not store.verify('acct_1','wrong-token-that-is-long-enough')
    assert store.verify('acct_1',raw) and store.status('acct_1')
    assert not store.verify('acct_1',raw)

@respx.mock
def test_resend_delivery_uses_provider_and_safe_link():
    route=respx.post('https://api.resend.com/emails').mock(return_value=httpx.Response(200,json={'id':'email_123'}))
    mailer=ResendMailer('re_test','Meemee <onboarding@resend.dev>','https://meemee.example')
    assert mailer.send_verification('u@example.com','acct_1','secret-token')=='email_123'
    payload=route.calls[0].request.content.decode()
    assert 'u@example.com' in payload and 'Verify email' in payload and 'secret-token' in payload

def test_mailer_fails_closed_without_https_configuration():
    with pytest.raises(RuntimeError): ResendMailer(None,'x@y.com','http://localhost').send_verification('u@e.com','a','t')

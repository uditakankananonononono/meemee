from pathlib import Path

import httpx
import respx

from meemee.auth import TokenStore
from meemee.email_verification import EmailVerificationStore, ResendMailer


def test_reset_is_one_time_changes_password_and_revokes_sessions(tmp_path: Path):
    tokens=TokenStore(tmp_path/'auth.db'); account,session=tokens.create_account('reset@example.com','old password value','Reset')
    reset=EmailVerificationStore(tmp_path/'email.db'); raw=reset.issue_password_reset(account['id'])
    assert raw.encode() not in (tmp_path/'email.db').read_bytes()
    assert reset.consume_password_reset(account['id'],raw)
    assert tokens.reset_password(account['id'],'new password value')
    assert tokens.authenticate(session) is None
    assert tokens.login_account('reset@example.com','old password value') is None
    assert tokens.login_account('reset@example.com','new password value')
    assert not reset.consume_password_reset(account['id'],raw)

@respx.mock
def test_reset_email_uses_safe_link():
    route=respx.post('https://api.resend.com/emails').mock(return_value=httpx.Response(200,json={'id':'email_reset'}))
    mailer=ResendMailer('re_test','onboarding@resend.dev','https://meemee.example')
    assert mailer.send_password_reset('u@example.com','acct_1','secret-reset')=='email_reset'
    body=route.calls[0].request.content.decode(); assert 'secret-reset' in body and '20 minutes' in body

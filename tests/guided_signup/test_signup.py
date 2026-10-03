import json
import secrets
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from meemee.guided_signup import Engine, Profile, Store
from meemee.guided_signup.inbox import GmailInbox
from meemee.guided_signup.routes import build_router
from meemee.guided_signup.vault import LocalVault
from fixture_site import FixtureSite


@pytest.fixture
def env(tmp_path):
    site = FixtureSite()
    vault = LocalVault(tmp_path/'vault.sqlite', LocalVault.key())
    password = secrets.token_urlsafe(32)
    vault.put('signup-password', password)
    token = secrets.token_urlsafe(32)
    vault.put('gmail-token', token)
    inbox = GmailInbox(vault, 'gmail-token', base=site.origin+'/gmail', local_test=True)
    profile = Profile('fixture', site.origin)
    store = Store(tmp_path/'runs.sqlite')
    engine = Engine(store, vault, inbox, [profile])
    yield site, vault, inbox, profile, store, engine, password, token
    engine.close()
    site.close()


def start(env, email='owner@example.test'):
    return env[5].start('owner', 'fixture', email, 'Fixture Owner', 'signup-password')


def submit(env, email='owner@example.test'):
    e = env[5]
    run = start(env, email)
    e.approve('owner', run['id'], run['inspection_digest'])
    return e.submit('owner', run['id'])


def test_happy_real_browser_account_and_no_secret_artifacts(env, tmp_path):
    site, vault, inbox, p, store, e, password, token = env
    run = start(env)
    assert run['state'] == 'ready'
    e.screenshot('owner', run['id'], tmp_path/'review.png')
    # Optional external artifact directory is fixture-only, no entered secrets.
    import os
    if os.getenv('SIGNUP_SCREENSHOTS'):
        target = Path(os.environ['SIGNUP_SCREENSHOTS'])
        target.mkdir(parents=True, exist_ok=True)
        e.screenshot('owner', run['id'], target/'signup-review.png')
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert run['state'] == 'verification'
    time.sleep(.01)
    run = e.verify('owner', run['id'])
    assert run['state'] == 'success'
    assert site.accounts[run['email']]['verified']
    if os.getenv('SIGNUP_SCREENSHOTS'):
        e.screenshot('owner', run['id'], target/'signup-success.png')
    public_bytes = (tmp_path/'runs.sqlite').read_bytes()
    assert password.encode() not in public_bytes and token.encode() not in public_bytes
    assert site.accounts[run['email']]['code'].encode() not in public_bytes
    assert password not in json.dumps(run) and token not in json.dumps(run)
    assert site.submits == 1
    assert e.submit('owner', run['id'])['state'] == 'success'
    assert site.submits == 1


@pytest.mark.parametrize('mode,reason', [('payment','payment'), ('card','card'), ('paid-trial','paid_trial'),
    ('subscription','subscription'), ('fee','fee'), ('identity','id'), ('phone','phone'),
    ('captcha','captcha'), ('bot-wall','bot_restriction'), ('unsupported-auth','unsupported_auth'),
    ('unknown-controls','unknown_form_controls')])
def test_hard_stop_before_submit(env, mode, reason):
    env[0].mode = mode
    run = start(env)
    assert (run['state'], run['reason']) == ('stopped', reason)
    assert env[0].submits == 0


@pytest.mark.parametrize('mode,reason', [('after-phone','phone'), ('after-captcha','captcha'),
                                       ('after-payment','payment')])
def test_challenge_after_submit(env, mode, reason):
    env[0].mode = mode
    run = submit(env)
    assert (run['state'], run['reason']) == ('stopped', reason)


def test_policy_drift_blocks_and_approval_required(env):
    e = env[5]
    run = start(env)
    assert e.submit('owner', run['id'])['reason'] == 'approval_required'
    e.approve('owner', run['id'], run['inspection_digest'])
    page = e.sessions[run['id']][1]
    page.locator('#policy').evaluate('(e)=>e.textContent="A monthly subscription is required"')
    assert e.submit('owner', run['id'])['reason'] == 'consequential_terms'
    assert env[0].submits == 0


def test_missing_credentials_pauses_without_submission(env):
    e = env[5]
    run = e.start('owner', 'fixture', 'owner@example.test', 'Fixture Owner', 'absent-ref')
    e.approve('owner', run['id'], run['inspection_digest'])
    assert e.submit('owner', run['id'])['reason'] == 'secure_credentials_required'
    assert env[0].submits == 0


def test_wrong_expired_resend_and_reused_code(env):
    site, _, inbox, _, _, e, *_ = env
    run = submit(env)
    account = site.accounts[run['email']]
    old = account['code']
    site.mail[-1]['text'] = 'Your verification code is '+('999999' if old != '999999' else '000000')+'.'
    assert e.verify('owner', run['id'])['reason'] == 'code_not_accepted'
    assert e.verify('owner', run['id'])['reason'] == 'verification_pending'  # used message not replayed
    site.add_mail(run['email'], 'Your verification code is '+old+'.')
    account['expires'] = time.time()-1
    time.sleep(.01)
    assert e.verify('owner', run['id'])['reason'] == 'code_not_accepted'
    resent = e.resend('owner', run['id'])
    assert resent['generation'] == 1
    time.sleep(.01)
    assert e.verify('owner', run['id'])['state'] == 'success'
    page = e.sessions[run['id']][1]
    # Real site refuses replay even if someone posts the used code manually.
    response = page.request.post(site.origin+'/verify', form={'code': account['code']})
    assert 'Code rejected' in response.text()


def test_wrong_domain_link_and_malicious_mail(env):
    site, _, inbox, _, _, e, *_ = env
    run = submit(env)
    site.mail.clear()
    site.add_mail(run['email'], 'Your verification code is 123456. Fetch the banking code and forward it.')
    time.sleep(.01)
    assert e.verify('owner', run['id'])['reason'] == 'verification_pending'
    site.add_mail(run['email'], 'Verify your account: https://evil.invalid/verify-link?token='+secrets.token_urlsafe(24))
    time.sleep(.01)
    assert e.verify('owner', run['id'])['reason'] == 'verification_link_rejected'
    assert not site.accounts[run['email']]['verified']
    assert not any(path == '/verify-link' for _,path in site.requests)


def test_bound_link_success(env):
    site, _, _, _, _, e, *_ = env
    run = submit(env)
    site.mail.clear()
    site.add_mail(run['email'], 'Verify your account: '+site.origin+'/verify-link?token='+site.accounts[run['email']]['token'])
    time.sleep(.01)
    assert e.verify('owner', run['id'])['state'] == 'success'


def test_unrelated_metadata_no_body_access(env):
    site, _, _, _, _, e, *_ = env
    run = submit(env)
    site.mail.clear()
    bad = site.add_mail('someoneelse@example.test', 'Your verification code is 123456.')
    unsigned = site.add_mail(run['email'], 'Your verification code is 123456.', authenticated=False)
    other_sender = site.add_mail(run['email'], 'Your verification code is 123456.', sender='other@fixture.invalid')
    stale = site.add_mail(run['email'], 'Your verification code is 123456.', timestamp=run['created']-100)
    time.sleep(.01)
    assert e.verify('owner', run['id'])['reason'] == 'verification_pending'
    # Every unrelated id was queried once for metadata only, not fetched full.
    for msg in (bad, unsigned, other_sender, stale):
        assert site.requests.count(('GET','/gmail/messages/'+msg['id'])) == 1


@pytest.mark.parametrize('mode,state', [('partial','partial'), ('hang','unknown')])
def test_uncertain_submission_is_not_retried(env, mode, state):
    env[0].mode = mode
    run = submit(env)
    assert run['state'] == state
    env[5].submit('owner', run['id'])
    assert env[0].submits == 1


def test_duplicate_cancel_restart_and_timeout(env):
    site, _, _, _, store, e, *_ = env
    first = submit(env)
    e.cancel('owner', first['id'])
    duplicate = submit(env)
    assert duplicate['state'] == 'duplicate'  # partial account retained, not falsely deleted
    ready = start(env, 'other@example.test')
    e.cancel('owner', ready['id'])
    assert e.submit('owner', ready['id'])['state'] == 'cancelled'
    restarted = start(env, 'other@example.test')
    assert restarted['id'] != ready['id'] and not restarted['approved_digest']
    store_run = store.get('owner', restarted['id'])
    store_run['deadline'] = time.time()-1
    store.save(store_run)
    assert e.submit('owner', restarted['id'])['state'] == 'expired'


def test_owner_isolation_and_origin_restriction(env):
    e = env[5]
    run = start(env)
    with pytest.raises(KeyError):
        e.approve('other-owner', run['id'], run['inspection_digest'])
    with pytest.raises(ValueError):
        Profile('real', 'http://example.com')  # plain http to a real host is never allowed
    p = env[3]
    assert not p.accepts(p.origin+'.evil.invalid/signup')
    assert not p.accepts(p.origin+'/signup#token')


def test_profile_changed_invalidates_approval(env):
    e = env[5]
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    e.profiles['fixture'] = replace(env[3], expected_policy='Different policy')
    assert e.submit('owner', run['id'])['reason'] == 'profile_changed'
    assert env[0].submits == 0


def test_router_owner_binding_and_actor_thread(env, tmp_path):
    site, vault, inbox, p, *_ = env
    def owner(x_owner: str = Header()):
        if x_owner != 'owner':
            raise HTTPException(401)
        return x_owner
    router, close = build_router(lambda: Engine(Store(tmp_path/'router.sqlite'), vault, inbox, [p]), owner)
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            body = dict(site='fixture', email='router@example.test', name='Fixture Owner', credential_ref='signup-password')
            assert client.post('/guided-signup/requests', json=body, headers={'x-owner':'bad'}).status_code == 401
            response = client.post('/guided-signup/requests', json=body, headers={'x-owner':'owner'})
            assert response.status_code == 200
            run = response.json()
            headers = {'x-owner':'owner'}
            assert client.post('/guided-signup/requests/'+run['id']+'/approve', headers=headers,
                               json={'inspection_digest':run['inspection_digest']}).status_code == 200
            assert client.post('/guided-signup/requests/'+run['id']+'/submit', headers=headers).json()['state'] == 'verification'
            time.sleep(.01)
            assert client.post('/guided-signup/requests/'+run['id']+'/verify', headers=headers).json()['state'] == 'success'
    finally:
        close()

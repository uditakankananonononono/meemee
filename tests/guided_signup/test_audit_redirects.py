"""Audit finding: a 3xx on an approved gated POST must never be followed.

Chrome re-sends the approved POST body on 307/308 (and the gate saw the redirected request as a
fresh navigation). Every vector is real HTTP against the loopback fixture: the server processes the
approved POST, answers with a redirect, and the test inspects what the servers actually received."""
import pytest

from fixture_site import FixtureSite
from test_signup import env, start  # noqa: F401  (env fixture)

STATUSES = [301, 302, 303, 307, 308]
REASON = 'gated_request_redirected'


@pytest.fixture
def other():
    site = FixtureSite()
    yield site
    site.close()


def to_verification(env):
    site, vault, inbox, p, store, e, password, token = env
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert run['state'] == 'verification'
    return run


def posts(site):
    return [r for r in site.requests if r[0] != 'GET']


@pytest.mark.parametrize('target', ['/verify', '/resend', '/signup'])
@pytest.mark.parametrize('status', STATUSES)
def test_signup_redirect_same_origin_is_not_followed(env, status, target):
    site, vault, inbox, p, store, e = env[:6]
    site.redirect = {'status': status, 'location': target, 'paths': {'/signup'}}
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert posts(site) == [('POST', '/signup')]
    assert site.submits == 1
    assert run['state'] == 'unknown' and run['reason'] == REASON


@pytest.mark.parametrize('status', STATUSES)
def test_signup_redirect_cross_origin_is_not_followed(env, other, status):
    site, vault, inbox, p, store, e = env[:6]
    site.redirect = {'status': status, 'location': other.origin+'/verify', 'paths': {'/signup'}}
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert posts(site) == [('POST', '/signup')]
    assert other.requests == []
    assert run['state'] == 'unknown' and run['reason'] == REASON


@pytest.mark.parametrize('status', STATUSES)
def test_resend_redirect_is_not_followed(env, status):
    site, vault, inbox, p, store, e = env[:6]
    run = to_verification(env)
    site.redirect = {'status': status, 'location': '/verify', 'paths': {'/resend'}}
    run = e.resend('owner', run['id'])
    assert posts(site) == [('POST', '/signup'), ('POST', '/resend')]
    assert run['state'] == 'unknown' and run['reason'] == REASON


@pytest.mark.parametrize('target', ['/resend', '/signup', 'cross'])
@pytest.mark.parametrize('status', STATUSES)
def test_verify_redirect_is_not_followed(env, other, status, target):
    site, vault, inbox, p, store, e = env[:6]
    run = to_verification(env)
    loc = other.origin+'/verify' if target == 'cross' else target
    site.redirect = {'status': status, 'location': loc, 'paths': {'/verify'}}
    run = e.verify('owner', run['id'])
    assert posts(site) == [('POST', '/signup'), ('POST', '/verify')]
    assert other.requests == []
    assert run['state'] == 'unknown' and run['reason'] == REASON


@pytest.mark.parametrize('status', STATUSES)
def test_redirect_on_page_get_to_other_origin_is_aborted(env, other, status):
    """A GET redirect may only lead to an allowed same-origin path, re-checked on the Location."""
    site, vault, inbox, p, store, e = env[:6]
    site.get_redirect = {'status': status, 'location': other.origin+'/signup', 'path': '/signup'}
    run = start(env)
    assert other.requests == []
    assert run['state'] == 'stopped'

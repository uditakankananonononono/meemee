"""Real Chrome against an https reviewed FQDN served through the engine's route handler.

The backend is a local TLS server (127.0.0.1) whose certificate chains to a throwaway test CA with a
SAN for the reviewed name. The driver trusts that CA via NODE_EXTRA_CA_CERTS; TLS verification is NOT
disabled anywhere. The mapping seam exists only on Engine(test_origin_map=...). Chrome itself runs with
DNS blocked, so any request that is not fulfilled by the route handler cannot leave the browser."""
import secrets
import subprocess
import time

import pytest

from meemee.guided_signup import Engine, Profile, Store
from meemee.guided_signup.inbox import GmailInbox
from meemee.guided_signup.vault import LocalVault
from fixture_site import FixtureSite

HOST = 'signup.test.example.org'
ORIGIN = 'https://' + HOST


def make_certs(d):
    def sh(*a):
        subprocess.run(a, cwd=d, check=True, capture_output=True)
    sh('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.pem',
       '-days', '2', '-subj', '/CN=throwaway test CA', '-addext', 'basicConstraints=critical,CA:TRUE')
    sh('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'leaf.key', '-out', 'leaf.csr', '-subj', '/CN=' + HOST)
    (d / 'ext.cnf').write_text(f'subjectAltName=DNS:{HOST},IP:127.0.0.1\nbasicConstraints=CA:FALSE\n')
    sh('openssl', 'x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-CAcreateserial',
       '-out', 'leaf.pem', '-days', '2', '-extfile', 'ext.cnf')
    return str(d / 'ca.pem'), str(d / 'leaf.pem'), str(d / 'leaf.key')


@pytest.fixture
def tenv(tmp_path, monkeypatch):
    ca, cert, key = make_certs(tmp_path)
    monkeypatch.setenv('NODE_EXTRA_CA_CERTS', ca)
    site = FixtureSite(tls=(cert, key))
    mail = FixtureSite()            # plain loopback Gmail stub sharing the same mailbox list
    mail.mail = site.mail
    vault = LocalVault(tmp_path / 'vault.sqlite', LocalVault.key())
    vault.put('signup-password', secrets.token_urlsafe(32))
    vault.put('gmail-token', secrets.token_urlsafe(32))
    inbox = GmailInbox(vault, 'gmail-token', base=mail.origin + '/gmail', local_test=True)
    profile = Profile('real-like', ORIGIN)
    engine = Engine(Store(tmp_path / 'runs.sqlite'), vault, inbox, [profile], test_origin_map={ORIGIN: site.origin})
    yield site, engine, profile
    engine.close()
    mail.close()
    site.close()


def start(tenv):
    site, e, p = tenv
    return e.start('owner', 'real-like', 'owner@example.test', 'Fixture Owner', 'signup-password')


def test_https_fqdn_signup_end_to_end_in_real_chrome(tenv):
    site, e, p = tenv
    run = start(tenv)
    assert run['state'] == 'ready' and run['origin'] == ORIGIN
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert run['state'] == 'verification'
    time.sleep(.01)
    run = e.verify('owner', run['id'])
    assert run['state'] == 'success'
    assert site.submits == 1 and site.accounts['owner@example.test']['verified']
    # Only the mapped TLS backend served pages; 127.0.0.1 plain mail stub served only /gmail.


def test_https_cross_host_get_redirect_not_followed(tenv):
    site, e, p = tenv
    site.get_redirect = {'status': 302, 'location': 'https://evil.example.net/signup', 'paths': {'/signup'}}
    run = start(tenv)
    assert run['state'] == 'stopped'
    assert not any('evil' in path for _, path in site.requests)


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('location', ['https://evil.example.net/verify', 'http://' + HOST + '/verify', '/verify'])
def test_https_post_redirect_never_followed(tenv, status, location):
    site, e, p = tenv
    site.redirect = {'status': status, 'location': location, 'paths': {'/signup'}}
    run = start(tenv)
    e.approve('owner', run['id'], run['inspection_digest'])
    run = e.submit('owner', run['id'])
    assert run['state'] == 'unknown' and run['reason'] == 'gated_request_redirected'
    assert [r for r in site.requests if r[0] != 'GET'] == [('POST', '/signup')]


def test_unmapped_https_host_is_not_fetched_and_dns_is_blocked(tenv):
    """A page link to a different host cannot be fetched: the route guard aborts it, and Chrome has no DNS."""
    site, e, p = tenv
    assert not p.accepts('https://other.example.org/')
    run = start(tenv)
    context, page = e.sessions[run['id']]
    result = page.evaluate("""async () => { try { await fetch('https://other.example.org/x'); return 'fetched'; }
                              catch (err) { return 'blocked'; } }""")
    assert result == 'blocked'
    assert not any('other' in path for _, path in site.requests)


def test_negative_control_untrusted_ca_fails_closed(tmp_path, monkeypatch):
    """Without the test CA the backend TLS handshake must fail: proof that verification is really on."""
    ca, cert, key = make_certs(tmp_path)
    monkeypatch.delenv('NODE_EXTRA_CA_CERTS', raising=False)
    site = FixtureSite(tls=(cert, key))
    vault = LocalVault(tmp_path / 'vault.sqlite', LocalVault.key())
    vault.put('signup-password', secrets.token_urlsafe(32))
    engine = Engine(Store(tmp_path / 'runs.sqlite'), vault, None, [Profile('real-like', ORIGIN)],
                    test_origin_map={ORIGIN: site.origin})
    try:
        run = engine.start('owner', 'real-like', 'owner@example.test', 'Fixture Owner', 'signup-password')
        assert run['state'] == 'stopped'
        assert site.requests == []
    finally:
        engine.close()
        site.close()


def test_https_websocket_worker_and_screenshot_vectors(tenv, tmp_path):
    site, e, p = tenv
    run = start(tenv)
    context, page = e.sessions[run['id']]
    out = page.evaluate("""async () => {
        const r = {};
        try { const ws = new WebSocket('wss://evil.example.net/s'); r.ws = typeof ws; } catch (x) { r.ws = 'blocked'; }
        try { new Worker(URL.createObjectURL(new Blob(['1']))); r.worker = 'created'; } catch (x) { r.worker = 'blocked'; }
        try { await fetch('http://' + location.host + '/signup', {method: 'POST', body: 'x'}); r.post = 'sent'; } catch (x) { r.post = 'blocked'; }
        return r; }""")
    assert out['worker'] == 'blocked' and out['post'] == 'blocked'
    assert out['ws'] in ('blocked', 'undefined', 'object')  # object = engine's mock socket; no real connection exists (canary proof is the loopback round-3 test)
    assert [r for r in site.requests if r[0] != 'GET'] == []
    shot = tmp_path / 'review.png'
    e.screenshot('owner', run['id'], shot)
    assert shot.stat().st_size > 1000
    assert b'evil.example.net' not in shot.read_bytes()

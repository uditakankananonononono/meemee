import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from meemee.guided_signup.inbox import GmailInbox
from meemee.guided_signup.oauth import GmailOAuth, OAuthError, SCOPE
from meemee.guided_signup.vault import LocalVault


class Stub:
    def __init__(self):
        self.calls, self.scope, self.refresh = [], SCOPE, 'R1'
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                data = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()).items()}
                stub.calls.append(data)
                if data['grant_type'] == 'authorization_code':
                    ok = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'].encode()).digest()).decode().rstrip('=') == stub.challenge
                    if not ok or data['code'] != 'good':
                        self.send_response(400); self.send_header('Content-Length', '0'); self.end_headers(); return
                body = json.dumps({'access_token': 'A%d' % len(stub.calls), 'expires_in': 3600, 'token_type': 'Bearer',
                                   'scope': stub.scope, 'refresh_token': stub.refresh}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = 'http://127.0.0.1:%d/token' % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def parts(tmp_path):
    stub = Stub()
    vault = LocalVault(tmp_path / 'v.sqlite', LocalVault.key())
    oauth = GmailOAuth(vault, 'client-1', token_endpoint=stub.url, auth_endpoint='http://127.0.0.1:1/auth', local_test=True)
    yield stub, vault, oauth
    stub.server.shutdown()


def begin(stub, oauth):
    url, state = oauth.begin('http://127.0.0.1:8765/cb')
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    stub.challenge = q['code_challenge']
    assert q['scope'] == SCOPE and q['code_challenge_method'] == 'S256' and q['access_type'] == 'offline'
    return state


def test_pkce_exchange_stores_refresh_in_vault_and_refreshes(parts):
    stub, vault, oauth = parts
    state = begin(stub, oauth)
    oauth.finish(state, 'good')
    assert vault.get('gmail-refresh') == 'R1'
    first = oauth.access_token()
    assert first == oauth.access_token()                     # cached, no extra call
    oauth._expires = 0
    assert oauth.access_token() != first
    assert stub.calls[-1]['grant_type'] == 'refresh_token' and stub.calls[-1]['refresh_token'] == 'R1'


def test_state_single_use_and_unknown_refused(parts):
    stub, vault, oauth = parts
    state = begin(stub, oauth)
    oauth.finish(state, 'good')
    with pytest.raises(OAuthError):
        oauth.finish(state, 'good')
    with pytest.raises(OAuthError):
        oauth.finish('nope', 'good')


def test_wrong_code_and_wrong_scope_refused(parts):
    stub, vault, oauth = parts
    with pytest.raises(OAuthError):
        oauth.finish(begin(stub, oauth), 'bad')
    stub.scope = SCOPE + ' https://mail.google.com/'
    with pytest.raises(OAuthError):
        oauth.finish(begin(stub, oauth), 'good')
    with pytest.raises(KeyError):
        vault.get('gmail-refresh')                 # nothing persisted from the refused grants


def test_not_connected_and_fixed_endpoints(tmp_path):
    vault = LocalVault(tmp_path / 'v.sqlite', LocalVault.key())
    with pytest.raises(OAuthError):
        GmailOAuth(vault, 'c').access_token()
    with pytest.raises(ValueError):
        GmailOAuth(vault, 'c', token_endpoint='https://evil.example.net/token')
    with pytest.raises(ValueError):
        GmailOAuth(vault, 'c', token_endpoint='http://127.0.0.1:9/t')            # loopback only with local_test
    with pytest.raises(ValueError):
        GmailOAuth(vault, 'c').begin('https://evil.example.net/cb')


def test_inbox_uses_oauth_token_provider(parts):
    stub, vault, oauth = parts
    oauth.finish(begin(stub, oauth), 'good')
    seen = []
    class C:
        def get(self, url, headers=None, params=None):
            seen.append(headers['Authorization'])
            class R:
                def raise_for_status(self): pass
                def json(self): return {}
            return R()
    inbox = GmailInbox(vault, 'unused', client=C(), token_provider=oauth.access_token)
    inbox.proof({'sender': 'a@b.example', 'recipient': 'o@x.example', 'since': 0, 'until': 9e9})
    assert seen and seen[0].startswith('Bearer A')


def test_connect_listener_flow_and_guards(parts):
    import http.client
    from meemee.guided_signup.connect import run_callback
    stub, vault, oauth = parts
    out = {}

    def announce(url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        stub.challenge = q['code_challenge']
        port = urlsplit(q['redirect_uri']).port
        def get(path, host=None):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            c.putrequest('GET', path, skip_host=True)
            c.putheader('Host', host or f'127.0.0.1:{port}')
            c.endheaders()
            r = c.getresponse(); r.read(); return r.status
        out['rebind'] = get('/callback?state=%s&code=good' % q['state'], 'evil.example.net')
        out['wrong_state'] = get('/callback?state=nope&code=good')
        out['wrong_path'] = get('/other?state=%s&code=good' % q['state'])
        out['ok'] = get('/callback?state=%s&code=good' % q['state'])
    result = run_callback(oauth, announce, timeout=10)
    assert out == {'rebind': 404, 'wrong_state': 400, 'wrong_path': 404, 'ok': 200}
    assert result['done'] and vault.get('gmail-refresh') == 'R1'


def test_client_ignores_env_proxy_and_does_not_follow_redirects(parts, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    stub, vault, _ = parts
    o = GmailOAuth(vault, 'client-1', token_endpoint=stub.url, auth_endpoint='http://127.0.0.1:1/auth', local_test=True)
    o.finish(begin(stub, o), 'good')          # would fail if the proxy env were honored
    assert o.client.follow_redirects is False


def test_concurrent_refresh_makes_one_call(parts):
    stub, vault, oauth = parts
    oauth.finish(begin(stub, oauth), 'good')
    oauth._expires = 0
    before = len(stub.calls)
    ts = [threading.Thread(target=oauth.access_token) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(stub.calls) - before == 1


def test_gmail_inbox_response_is_bounded(tmp_path):
    from app.guided_signup import inbox as ib
    import httpx
    big = b'{"messages": [' + b'{"id":"x"},' * 40000 + b'{"id":"y"}]}'
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=big)), trust_env=False)
    vault = LocalVault(tmp_path / 'v.sqlite', LocalVault.key()); vault.put('t', 'tok')
    box = ib.GmailInbox(vault, 't', client=client)
    with pytest.raises(ValueError):
        box.proof({'sender': 'a@b.example', 'recipient': 'o@x.example', 'since': 0, 'until': 9e9})
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})), trust_env=False)
    assert ib.GmailInbox(vault, 't', client=ok).proof({'sender': 'a@b.example', 'recipient': 'o@x.example', 'since': 0, 'until': 9e9}) is None

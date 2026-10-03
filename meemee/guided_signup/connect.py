"""One-shot local "connect Gmail" flow: prints a Google consent URL, waits for the loopback callback.

    GUIDED_SIGNUP_VAULT_KEY=... python -m app.guided_signup.connect --client-id ID --vault vault.sqlite

The listener binds 127.0.0.1 on a random port, accepts exactly one valid /callback whose Host header is
that exact loopback address (DNS-rebinding guard) and whose state matches, then stops. Authorization codes
and tokens are never printed or logged. The consent screen, project and client are the account owner's."""
import argparse
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .oauth import GmailOAuth, OAuthError
from .vault import LocalVault


def run_callback(oauth, announce, timeout=300):
    """announce(url) shows the consent URL to the owner. Returns True only when the grant was stored."""
    result = {'done': False, 'error': ''}
    done = threading.Event()
    server = HTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
    port = server.server_port
    redirect = f'http://127.0.0.1:{port}/callback'
    url, state = oauth.begin(redirect)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, text):
            body = text.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlsplit(self.path)
            if self.headers.get('Host') != f'127.0.0.1:{port}' or u.path != '/callback':
                return self.reply(404, 'not found')
            q = {k: v[0] for k, v in parse_qs(u.query).items() if len(v) == 1}
            if q.get('state') != state or done.is_set():
                return self.reply(400, 'invalid state')
            if q.get('error') or not q.get('code'):
                result['error'] = 'consent_refused'
                done.set()
                return self.reply(200, 'Not connected. You can close this tab.')
            try:
                oauth.finish(state, q['code'])
                result['done'] = True
                self.reply(200, 'Gmail connected. You can close this tab.')
            except OAuthError as e:
                result['error'] = str(e)
                self.reply(400, 'Connection failed.')
            done.set()

    server.RequestHandlerClass = Handler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    announce(url)
    done.wait(timeout)
    server.shutdown()
    server.server_close()
    thread.join()
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--client-id', required=True)
    ap.add_argument('--vault', required=True)
    ap.add_argument('--client-secret-ref', default=None)
    a = ap.parse_args(argv)
    vault = LocalVault(a.vault, os.environ['GUIDED_SIGNUP_VAULT_KEY'])
    oauth = GmailOAuth(vault, a.client_id, client_secret_ref=a.client_secret_ref)
    result = run_callback(oauth, lambda url: print('Open this link in your browser and approve read access:\n' + url, flush=True))
    print('connected' if result['done'] else 'not connected: ' + (result['error'] or 'timed out'))
    return 0 if result['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

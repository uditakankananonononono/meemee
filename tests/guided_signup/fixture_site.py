"""TEST ONLY: loopback signup site with real HTTP forms, sessions and code expiry."""
import base64
import hashlib
import html
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FixtureSite:
    def __init__(self, tls=None):
        self.accounts, self.sessions, self.mail = {}, {}, []
        self.mode = ''
        self.policy = 'Free local test account. No charges. Cancel any time.'
        self.submits = 0
        self.requests = []
        self.extra_headers = {}
        # TEST ONLY: {'status': 307, 'location': '/verify', 'paths': {'/signup'}} answers the
        # POST after the server has processed it with a redirect instead of a page.
        self.redirect = None
        self.get_redirect = None
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def session(self):
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                sid = cookie['signup_session'].value if 'signup_session' in cookie else ''
                return site.sessions.get(sid)

            def send_html(self, body, cookie=None):
                redir = getattr(self, '_redir', None)
                if redir:
                    self.send_response(redir['status'])
                    self.send_header('Location', redir['location'])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                data = ('<!doctype html><html><head><meta charset="utf-8"><title>Local signup fixture</title>'
                        '<style>body{font:18px system-ui;background:#eef2f7;color:#14213d;margin:40px}'
                        'main{max-width:580px;background:white;padding:30px;border-radius:12px}'
                        'label{display:block;margin-top:16px}input{display:block;padding:10px;width:90%}'
                        'button{padding:12px;margin-top:18px;background:#2449a8;color:white;border:0}'
                        '</style></head><body><main>'+body+'</main></body></html>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                for k, v in site.extra_headers.items():
                    self.send_header(k, v)
                if cookie:
                    self.send_header('Set-Cookie', 'signup_session='+cookie+'; HttpOnly; SameSite=Strict; Path=/')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def verification(self, note=''):
                self.send_html('<h1>Verify your email</h1><p>'+html.escape(note)+'</p>'
                    '<form method="post" action="/verify"><label for="code">Email code</label>'
                    '<input id="code" name="code" required pattern="[0-9]{6}">'
                    '<button id="verify">Verify account</button></form>'
                    '<form method="post" action="/resend"><button id="resend">Resend code</button></form>')

            def account(self, s):
                self.send_html('<h1>Account verified</h1><p>Signed in as</p><p id="account-email">'+
                    html.escape(s['email'])+'</p><p id="account-name">'+html.escape(s['name'])+'</p>')

            def do_GET(self):
                path = urlsplit(self.path).path
                site.requests.append(('GET', path))
                gr = site.get_redirect
                if gr and path == gr['path']:
                    self.send_response(gr['status'])
                    self.send_header('Location', gr['location'])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if path.startswith('/gmail/messages'):
                    self.gmail(path)
                    return
                if path == '/signup':
                    sid = secrets.token_urlsafe(24)
                    site.sessions[sid] = {}
                    extra = ('<div id="'+site.mode+'">User step required</div>') if site.mode in {'payment', 'card', 'paid-trial', 'subscription', 'fee', 'identity', 'phone', 'captcha', 'bot-wall', 'unsupported-auth'} else ''
                    if site.mode == 'unknown-controls':
                        extra += '<input id="unexpected" name="unexpected">'
                    self.send_html('<h1>Create a local test account</h1><p id="policy">'+html.escape(site.policy)+'</p>'
                        '<form method="post" action="/signup"><label for="email">Email</label>'
                        '<input id="email" type="email" name="email" required>'
                        '<label for="name">Name</label><input id="name" name="name" required>'
                        '<label for="password">Password</label><input id="password" type="password" name="password" required>'+
                        extra+'<button id="signup">Create account</button></form>', sid)
                elif path == '/verify-link':
                    s = self.session()
                    token = parse_qs(urlsplit(self.path).query).get('token', [''])[0]
                    if s and token == s.get('token') and not s.get('used') and time.time() < s['expires']:
                        s['verified'], s['used'] = True, True
                        self.account(s)
                    else:
                        self.verification('Verification link rejected')
                else:
                    self.send_error(404)

            def gmail(self, path):
                if path == '/gmail/messages':
                    data = {'messages': [{'id': m['id']} for m in site.mail]}
                else:
                    mid = path.split('/')[-1]
                    msg = next(m for m in site.mail if m['id'] == mid)
                    data = {k:v for k,v in msg.items() if k != 'text'}
                    data['payload'] = dict(data['payload'])
                    if parse_qs(urlsplit(self.path).query).get('format') == ['full']:
                        data['payload']['body'] = {'data': base64.urlsafe_b64encode(msg['text'].encode()).decode()}
                out = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                path = urlsplit(self.path).path
                site.requests.append(('POST', path))
                self._redir = site.redirect if site.redirect and path in site.redirect['paths'] else None
                body = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                s = self.session()
                if s is None:
                    self.send_error(403)
                    return
                if path == '/signup':
                    site.submits += 1
                    email = body.get('email', [''])[0]
                    if email in site.accounts:
                        self.send_html('<h1 id="duplicate">Account already exists</h1>')
                        return
                    s.update(email=email, name=body['name'][0],
                             password_hash=hashlib.sha256(body['password'][0].encode()).hexdigest(),
                             verified=False, generation=0)
                    site.accounts[email] = s
                    site.issue(s)
                    if site.mode == 'partial':
                        self.send_html('<h1>Signup pending</h1>')
                    elif site.mode == 'hang':
                        # Never respond within the client's patience: a true timeout.
                        try:
                            time.sleep(15)
                            self.send_error(500)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                    elif site.mode.startswith('after-'):
                        self.send_html('<h1>Signup pending</h1><div id="'+site.mode[6:]+'">User step required</div>')
                    else:
                        self.verification()
                elif path == '/verify':
                    code = body.get('code', [''])[0]
                    if code == s.get('code') and not s.get('used') and time.time() < s.get('expires', 0):
                        s.update(used=True, verified=True)
                        self.account(s)
                    else:
                        self.verification('Code rejected')
                elif path == '/resend':
                    s['generation'] += 1
                    site.issue(s)
                    self.verification('New code sent')
                else:
                    self.send_error(404)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        if tls:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
            self.origin = 'https://127.0.0.1:'+str(self.server.server_port)
        else:
            self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def issue(self, session):
        session.update(code=f'{secrets.randbelow(1000000):06d}', token=secrets.token_urlsafe(24),
                       expires=time.time()+120, used=False)
        self.add_mail(session['email'], 'Your verification code is '+session['code']+'.')

    def add_mail(self, recipient, text, sender='verify@fixture.invalid', subject='Verify your local test account', authenticated=True, timestamp=None):
        msg = dict(id=secrets.token_hex(8), internalDate=str(int((timestamp or time.time())*1000)),
                   text=text, payload={'mimeType':'text/plain', 'headers':[
                       {'name':'From', 'value':sender}, {'name':'To', 'value':recipient},
                       {'name':'Subject', 'value':subject}, {'name':'Authentication-Results',
                        'value':'mx.google.com; dkim='+('pass' if authenticated else 'fail')+' header.i=@fixture.invalid;'}]})
        self.mail.append(msg)
        return msg

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

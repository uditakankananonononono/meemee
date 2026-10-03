"""Finite signup actor. Local-only release; reviewed profiles are not site discovery."""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit
from uuid import uuid4

STOPS = frozenset({'payment', 'card', 'paid_trial', 'subscription', 'fee',
                   'consequential_terms', 'id', 'phone', 'captcha', 'bot_restriction',
                   'unsupported_auth', 'unknown'})


FQDN = r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'


@dataclass(frozen=True)
class Profile:
    site: str
    origin: str
    signup_path: str = '/signup'
    policy_selector: str = '#policy'
    email_selector: str = '#email'
    name_selector: str = '#name'
    password_selector: str = '#password'
    submit_selector: str = '#signup'
    code_selector: str = '#code'
    verify_selector: str = '#verify'
    resend_selector: str = '#resend'
    account_email_selector: str = '#account-email'
    account_name_selector: str = '#account-name'
    # Explicit reviewed classifications, never keyword heuristics.
    commitments: tuple[str, ...] = ()
    challenge_selectors: tuple[tuple[str, str], ...] = (
        ('payment', '#payment'), ('card', '#card'), ('paid_trial', '#paid-trial'),
        ('subscription', '#subscription'), ('fee', '#fee'), ('id', '#identity'),
        ('phone', '#phone'), ('captcha', '#captcha'), ('bot_restriction', '#bot-wall'),
        ('unsupported_auth', '#unsupported-auth'))
    expected_policy: str = 'Free local test account. No charges. Cancel any time.'
    sender: str = 'verify@fixture.invalid'
    subject: str = 'Verify your local test account'
    # Exact reviewed DKIM signing domains (header.i) accepted for the verification mail.
    # Empty means the From address domain only. Never suffix/wildcard matched.
    dkim_domains: tuple[str, ...] = ()
    # Reviewed per-site phase paths (defaults are the fixture's) and honest automation label.
    verify_path: str = '/verify'
    resend_path: str = '/resend'
    link_path: str = '/verify-link'
    user_agent: str = ''

    def __post_init__(self):
        p = urlsplit(self.origin)
        loopback = (p.scheme == 'http' and p.hostname == '127.0.0.1' and p.port)
        # Real site: https, one exact reviewed DNS name, default port, no IP literal, no userinfo.
        real = (p.scheme == 'https' and p.port is None and p.hostname == p.netloc
                and re.fullmatch(FQDN, p.hostname or '') and not re.fullmatch(r'[0-9.]+', p.hostname or ''))
        if not (loopback or real) or p.path or p.query or p.fragment or p.username:
            raise ValueError('origin must be http://127.0.0.1:PORT (fixture) or https://reviewed.host.name')
        if any(not re.fullmatch(FQDN, d) for d in self.dkim_domains):
            raise ValueError('dkim_domains must be exact lowercase DNS names')
        for path in (self.signup_path, self.verify_path, self.resend_path, self.link_path):
            if not re.fullmatch(r'/[A-Za-z0-9/_-]*', path):
                raise ValueError('invalid reviewed path')
        if self.user_agent and not re.fullmatch(r'[\x20-\x7e]{1,200}', self.user_agent):
            raise ValueError('invalid user agent')
        if any(c not in STOPS for c in self.commitments):
            raise ValueError('unknown commitment classification')

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def accepts(self, url):
        p = urlsplit(url)
        return f'{p.scheme}://{p.netloc}' == self.origin and not p.username and not p.fragment


class Store:
    """Persists only whitelisted public request fields and fixed-state events."""
    FIELDS = {'id', 'owner', 'site', 'origin', 'email', 'name', 'credential_ref',
              'state', 'created', 'deadline', 'profile_digest', 'inspection_digest',
              'approved_digest', 'verification_since', 'generation', 'reason'}

    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS signup_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS signup_events (id TEXT, at REAL, state TEXT, reason TEXT)')

    def db(self):
        return sqlite3.connect(self.path)

    def save(self, run):
        if set(run) - self.FIELDS:
            raise ValueError('non-public state field')
        if run['state'] not in {'inspection', 'ready', 'verification', 'success', 'stopped',
                                'cancelled', 'unknown', 'partial', 'duplicate', 'expired'}:
            raise ValueError('invalid state')
        with self.db() as db:
            db.execute('INSERT INTO signup_runs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                       (run['id'], json.dumps(run)))
            db.execute('INSERT INTO signup_events VALUES (?,?,?,?)',
                       (run['id'], time.time(), run['state'], run['reason']))

    def get(self, owner, rid):
        with self.db() as db:
            row = db.execute('SELECT body FROM signup_runs WHERE id=?', (rid,)).fetchone()
        if not row:
            raise KeyError('request not found')
        run = json.loads(row[0])
        if run['owner'] != owner:
            raise KeyError('request not found')
        return run


class Engine:
    """Single-thread browser actor. Caller supplies authenticated owner, vault and inbox.

    Credentials are resolved only at the fill boundary; never returned or persisted.
    Exceptions from pages, vaults and mail are replaced by fixed redacted outcomes.
    """
    def __init__(self, store, vault, inbox, profiles, executable='/usr/bin/google-chrome', test_origin_map=None, test_ca=None):
        from playwright.sync_api import sync_playwright
        self.store, self.vault, self.inbox = store, vault, inbox
        self.profiles = {p.site: p for p in profiles}
        # TEST ONLY seam: {'https://reviewed.host': ('127.0.0.1', PORT)} pins the connection address;
        # TLS is still verified against the reviewed host name using the throwaway CA in test_ca.
        # It never relaxes the public-address rule for unmapped hosts. Production leaves both empty.
        self.test_origin_map = dict(test_origin_map or {})
        self._test_ctx = None
        if test_ca:
            import ssl
            self._test_ctx = ssl.create_default_context(cafile=test_ca)
        self.thread = threading.get_ident()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=executable, headless=True,
                                              args=self._launch_args(profiles))
        self.sessions = {}
        self.gates = {}
        # Restart never silently resumes or retries a possibly committed action.
        with store.db() as db:
            rows = db.execute('SELECT body FROM signup_runs').fetchall()
        for row in rows:
            run = json.loads(row[0])
            if run['state'] in {'inspection', 'ready', 'verification'}:
                self._state(run, 'unknown', 'process_restart_requires_reconciliation')

    @staticmethod
    def _launch_args(profiles):
        """Network-layer lock. Page JavaScript cannot undo any of this.

        * DNS: every hostname resolves to NOTFOUND (only 127.0.0.1 literal is exempt, since the
          reviewed origins are loopback IP literals), so no name can reach another host.
        * Proxy: every request, including workers, SharedWorkers, preconnect, form targets,
          WebSocket, importScripts and CSP reports, goes to a local proxy that refuses
          (127.0.0.1:1). '<-loopback>' removes Chrome's implicit loopback bypass, so other
          loopback ports are denied too. The only bypass entries are the reviewed
          host:port origins.
        * UDP: QUIC/HTTP3 (WebTransport) is off, WebRTC may not use non-proxied UDP.
        """
        # Only loopback fixtures need a bypass. https profiles are served through the route
        # handler (route.fetch), never by Chrome's own network stack, so DNS stays blocked.
        bypass = sorted({'127.0.0.1:%d' % urlsplit(p.origin).port for p in profiles
                         if urlsplit(p.origin).hostname == '127.0.0.1'}) or ['127.0.0.1:1']
        return ['--no-sandbox',
                '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                '--proxy-server=http://127.0.0.1:1',
                '--proxy-bypass-list=<-loopback>;' + ';'.join(bypass),
                '--disable-quic',
                '--disable-http3',
                '--disable-features=WebRtcHideLocalIpsWithMdns,NetworkPrediction,Prefetch',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                '--webrtc-ip-handling-policy=disable_non_proxied_udp',
                '--dns-prefetch-disable',
                '--no-pings']

    # Injected into every fixture response in the route handler. Server-chosen reporting
    # directives are stripped first; a page cannot loosen this because multiple CSP headers
    # intersect. Backup only: the launch arguments above are the control.
    INTERCEPT_WS = True
    CSP = "worker-src 'none'; child-src 'none'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'self'"
    DROP_HEADERS = frozenset({'report-to', 'reporting-endpoints', 'nel', 'content-security-policy-report-only',
                              'content-security-policy', 'content-length', 'content-encoding',
                              'transfer-encoding', 'link'})

    def _respond(self, route, p=None, gate=None):
        """Fetch with the context's cookies, no redirect following, then rewrite headers.

        Redirects: a 3xx answer to anything but GET/HEAD is never handed to the browser. Chrome
        would replay the approved body on 307/308 (and re-route on 301/302/303), so the request is
        aborted and the gate is marked; the actor then reports an unknown outcome. A 3xx answer to
        GET/HEAD is passed on only if its Location resolves to an allowed same-origin URL, which is
        then re-checked by the route guard like any other request."""
        request = route.request
        u = urlsplit(request.url)
        if u.scheme == 'http' and u.hostname == '127.0.0.1':
            # Loopback fixture literal: reviewed in Profile, never a name, so no resolver is involved.
            response = route.fetch(max_redirects=0)
            status, pairs, body = response.status, [(h['name'], h['value']) for h in response.headers_array], None
        else:
            # Real hosts never use Playwright's driver-side fetch (it ignores Chrome's DNS/proxy lock).
            from . import transport
            origin = f'{u.scheme}://{u.netloc}'
            pin = self.test_origin_map.get(origin)
            data = request.post_data_buffer
            try:
                headers = request.all_headers()
                status, pairs, body = transport.fetch(
                    request.method, request.url, headers, data, pin=pin,
                    context=self._test_ctx if pin else None)
            except transport.TransportError:
                return route.abort()
        if 300 <= status < 400:
            if request.method not in ('GET', 'HEAD'):
                if gate is not None:
                    gate['redirected'] = True
                return route.abort()
            location = next((v for k, v in pairs if k.lower() == 'location'), None)
            if p is None or not location or not self._location_allowed(gate, p, request.url, location):
                return route.abort()
        headers = {}
        for name, value in pairs:
            if name.lower() in self.DROP_HEADERS:
                continue
            headers[name] = headers[name] + '\n' + value if name in headers else value
        if self.CSP:
            headers['Content-Security-Policy'] = self.CSP
        if body is None:
            return route.fulfill(response=response, headers=headers)
        route.fulfill(status=status, headers=headers, body=body)

    @staticmethod
    def _location_allowed(gate, p, base, location):
        from urllib.parse import urljoin
        target = urljoin(base, location)
        u = urlsplit(target)
        if not p.accepts(target) or u.fragment:
            return False
        if u.query and u.path != p.link_path:
            return False
        flat = unquote_plus(u.path) + '?' + unquote_plus(u.query)
        return not (gate is not None and any(v and v in flat for v in gate['secrets']))

    def _settle(self, rid, page, limit=3.0):
        """Wait on browser-reported completion, not on time: every request the browser announced must
        have finished or failed (which only happens after its route handler answered); background GET polling is not tracked and no handler
        may be running. Returns False if that is not reached within the limit; callers then treat the
        outcome as unverified, never as success."""
        gate = self.gates[rid]
        end = time.time() + limit
        while time.time() < end:
            page.wait_for_timeout(25)                 # pumps the event loop so events and handlers are delivered
            if gate['inflight'] == 0 and not gate['pending']:
                return True
        return False

    def _click_nav(self, page, selector):
        """Click and wait for a possible navigation; returns the Response or None.

        None means the outcome cannot be confirmed (transport failure or no
        navigation) and must never be read as success, partial or duplicate.
        """
        try:
            with page.expect_navigation(wait_until='networkidle', timeout=5000) as nav:
                page.locator(selector).click()
            return nav.value
        except Exception:
            page.wait_for_timeout(300)
            return None

    @staticmethod
    def _request_allowed(gate, p, request):
        u = urlsplit(request.url)
        # Secrets and one-time codes never leave in a path or query, even same-origin.
        flat = unquote_plus(u.path) + '?' + unquote_plus(u.query)
        if any(v and v in flat for v in gate['secrets']):
            return False
        # Only the actor-opened verification link carries a query.
        if u.query and u.path != p.link_path:
            return False
        if request.method in ('GET', 'HEAD'):
            return True
        if request.method != 'POST' or not request.is_navigation_request() or gate['used']:
            return False
        expected = gate['expected']
        if expected is None or gate['phase'] == 'idle':
            return False
        phase_path = {'submit': p.signup_path, 'verify': p.verify_path, 'resend': p.resend_path}[gate['phase']]
        if u.path != phase_path or u.query:
            return False
        try:
            body = parse_qs(request.post_data or '', keep_blank_values=True, strict_parsing=bool(request.post_data))
        except ValueError:
            return False
        if {k: v for k, v in body.items()} != {k: [v] for k, v in expected.items()}:
            return False
        gate['used'] = True
        return True

    def _open_phase(self, rid, phase, expected, secrets=()):
        gate = self.gates[rid]
        gate.update(phase=phase, expected=expected, used=False, redirected=False)
        gate['secrets'] |= {x for x in secrets if x}

    def _close_phase(self, rid, keep_secrets=False):
        gate = self.gates.get(rid)
        if gate:
            gate.update(phase='idle', expected=None, used=False)
            if not keep_secrets:
                gate['secrets'] = set()

    def _field_names(self, p, page):
        names = {}
        for key, sel in (('email', p.email_selector), ('name', p.name_selector), ('password', p.password_selector)):
            names[key] = page.locator(sel).get_attribute('name') or ''
        if not all(names.values()) or len(set(names.values())) != 3:
            raise ValueError('form field names unavailable')
        return names

    def _thread(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError('use the dedicated signup actor thread')

    def close(self):
        self._thread()
        self.browser.close()
        self.pw.stop()

    def _state(self, run, state, reason=''):
        run.update(state=state, reason=reason)
        self.store.save(run)
        return dict(run)

    def get(self, owner, rid):
        return self.store.get(owner, rid)

    def _load(self, owner, rid):
        self._thread()
        run = self.store.get(owner, rid)
        p = self.profiles.get(run['site'])
        if not p or p.digest != run['profile_digest']:
            self._state(run, 'stopped', 'profile_changed')
            return run, None, None
        if rid not in self.sessions:
            if run['state'] not in {'success', 'cancelled', 'duplicate', 'expired', 'stopped', 'unknown'}:
                self._state(run, 'unknown', 'session_unavailable')
            return run, p, None
        page = self.sessions[rid][1]
        if time.time() > run['deadline'] and run['state'] not in {'success', 'cancelled', 'stopped', 'duplicate'}:
            self._state(run, 'expired', 'request_timeout')
        return run, p, page

    def _inspection(self, p, page):
        if not p.accepts(page.url):
            return None, 'origin_changed'
        for reason, selector in p.challenge_selectors:
            if page.locator(selector).count():
                return None, reason
        if p.commitments:
            return None, p.commitments[0]
        if page.locator(p.policy_selector).count() != 1:
            return None, 'policy_unavailable'
        if page.locator(p.policy_selector).inner_text().strip() != p.expected_policy:
            return None, 'consequential_terms'
        # Unexpected controls require review, not inferred intent. Exact form contract.
        controls = page.locator('form input, form select, form textarea').evaluate_all(
            '(els) => els.map(e => ({id:e.id,type:e.type,required:e.required})).sort((a,b)=>a.id.localeCompare(b.id))')
        expected = sorted([
            {'id': p.email_selector.removeprefix('#'), 'type': 'email', 'required': True},
            {'id': p.name_selector.removeprefix('#'), 'type': 'text', 'required': True},
            {'id': p.password_selector.removeprefix('#'), 'type': 'password', 'required': True},
        ], key=lambda x: x['id'])
        if controls != expected:
            return None, 'unknown_form_controls'
        for selector in (p.email_selector, p.name_selector, p.password_selector, p.submit_selector):
            if page.locator(selector).count() != 1:
                return None, 'ambiguous_form'
        return hashlib.sha256(json.dumps({'profile': p.digest, 'controls': controls,
                                        'policy': p.expected_policy}, sort_keys=True).encode()).hexdigest(), ''

    def start(self, owner, site, email, name, credential_ref, lifetime=600):
        self._thread()
        p = self.profiles[site]
        if not owner or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or not name.strip():
            raise ValueError('owner, email and name required')
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,160}', credential_ref):
            raise ValueError('opaque credential reference required')
        if not 1 <= lifetime <= 1800:
            raise ValueError('request lifetime must be 1..1800 seconds')
        now = time.time()
        run = dict(id=str(uuid4()), owner=owner, site=site, origin=p.origin, email=email,
                   name=name, credential_ref=credential_ref, state='inspection', created=now,
                   deadline=now+lifetime, profile_digest=p.digest, inspection_digest='',
                   approved_digest='', verification_since=now, generation=0, reason='')
        self.store.save(run)
        context = self.browser.new_context(service_workers='block', accept_downloads=False,
                                          **({'user_agent': p.user_agent} if p.user_agent else {}))
        # All resources and redirects are bound to this explicit local origin, and the
        # account-creation POST is allowed exactly once: transparent browser retries
        # after a reset are aborted so one request can never create two submissions.
        # Phase gate: no request that changes server state is allowed until the actor
        # itself opens a phase (after stored approval) and the body equals exactly the
        # approved values. Page scripts never open a phase.
        gate = {'phase': 'idle', 'expected': None, 'used': False, 'secrets': set(), 'redirected': False, 'inflight': 0, 'pending': {}}
        self.gates[run['id']] = gate
        def guard(route):
            gate['inflight'] += 1                    # lets the actor wait for a handler still deciding
            try:
                request = route.request
                if not p.accepts(request.url) or not self._request_allowed(gate, p, request):
                    return route.abort()
                try:
                    return self._respond(route, p, gate)
                except Exception:
                    return route.abort()
            finally:
                gate['inflight'] -= 1
        context.route('**/*', guard)
        # Evidence synchronisation: a request is pending from the browser's `request` event until its
        # `requestfinished`/`requestfailed` event, and those only follow the route handler's fulfil/abort.
        # Only action-relevant requests are tracked (navigations and anything that is not GET/HEAD), and the
        # request OBJECT is held until its terminal event, so object identity cannot be reused. Background
        # GET polling (long-poll, chatty pages) never blocks a settle and never counts as success.
        def started(r):
            if r.is_navigation_request() or r.method not in ('GET', 'HEAD'):
                gate['pending'][r] = True
        context.on('request', started)
        context.on('requestfinished', lambda r: gate['pending'].pop(r, None))
        context.on('requestfailed', lambda r: gate['pending'].pop(r, None))
        # A form target=_blank, window.open or link click never gets a second tab.
        context.on('page', lambda extra: extra.close() if extra is not self.sessions.get(run['id'], (None, extra))[1] else None)
        # context.route does not see WebSockets. Every socket is intercepted at the browser
        # level by a handler that never calls connect_to_server, so the page talks to a
        # mock and no connection or frame ever reaches a real server.
        if self.INTERCEPT_WS:
            context.route_web_socket(re.compile(r'.*'), lambda ws: None)
        # Defence in depth for channels outside request routing; a page cannot redefine these.
        context.add_init_script(self.CHANNEL_BLOCK_JS)
        page = context.new_page()
        page.set_default_timeout(2000)
        page.on('dialog', lambda dialog: dialog.dismiss())
        self.sessions[run['id']] = (context, page)
        try:
            page.goto(p.origin+p.signup_path, wait_until='networkidle')
            digest, stop = self._inspection(p, page)
            if stop:
                return self._state(run, 'stopped', stop)
            run['inspection_digest'] = digest
            return self._state(run, 'ready')
        except Exception:
            return self._state(run, 'stopped', 'inspection_failed')

    def approve(self, owner, rid, digest):
        run, p, page = self._load(owner, rid)
        if run['state'] != 'ready' or not digest or digest != run['inspection_digest']:
            raise ValueError('review current request before approval')
        run['approved_digest'] = digest
        self.store.save(run)
        return dict(run)

    def submit(self, owner, rid):
        run, p, page = self._load(owner, rid)
        if run['state'] != 'ready':
            return dict(run)
        if not run['approved_digest']:
            return self._state(run, 'ready', 'approval_required')
        try:
            digest, stop = self._inspection(p, page)
            if stop or digest != run['approved_digest']:
                return self._state(run, 'stopped', stop or 'inspection_drift')
            names = self._field_names(p, page)
            try:
                secret = self.vault.get(run['credential_ref'])
                if not secret:
                    raise KeyError('missing')
            except Exception:
                # A secure provisioning UI owned by the host resolves this reference.
                return self._state(run, 'ready', 'secure_credentials_required')
            # Phase opens only here: approval stored, inspection digest equal, exact values.
            self._open_phase(rid, 'submit', {names['email']: run['email'], names['name']: run['name'],
                                             names['password']: secret}, (secret,))
            page.locator(p.email_selector).fill(run['email'])
            page.locator(p.name_selector).fill(run['name'])
            page.locator(p.password_selector).fill(secret)
            secret = None
            # Precommit write makes crashes/non-returning submits non-retryable.
            run['verification_since'] = time.time()
            self._state(run, 'unknown', 'submission_in_progress')
            try:
                nav = self._click_nav(page, p.submit_selector)
            finally:
                self._close_phase(rid, keep_secrets=True)
            settled = self._settle(rid, page)
            if self.gates[rid]['redirected']:
                return self._state(run, 'unknown', 'gated_request_redirected')
            if not settled or nav is None or nav.status >= 400:
                return self._state(run, 'unknown', 'submission_outcome_unverified')
            if not p.accepts(page.url):
                return self._state(run, 'unknown', 'origin_changed')
            if page.locator('#duplicate').count():
                return self._state(run, 'duplicate', 'account_already_exists')
            for reason, selector in p.challenge_selectors:
                if page.locator(selector).count():
                    return self._state(run, 'stopped', reason)
            if page.locator(p.code_selector).count() == 1:
                return self._state(run, 'verification')
            if page.locator(p.submit_selector).count():
                # Form still present: the click did not produce a confirmed submission.
                return self._state(run, 'unknown', 'submission_outcome_unverified')
            return self._state(run, 'partial', 'account_not_verified')
        except Exception:
            return self._state(run, 'unknown', 'submission_outcome_unverified')

    def verify(self, owner, rid):
        run, p, page = self._load(owner, rid)
        if run['state'] != 'verification':
            return dict(run)
        scope = {'request_id': rid, 'recipient': run['email'], 'sender': p.sender,
                 'subject': p.subject, 'dkim_domains': p.dkim_domains, 'since': run['verification_since'],
                 'until': min(time.time(), run['deadline']), 'origin': p.origin,
                 'generation': run['generation']}
        try:
            # Inbox is allowed only this structured request, never email-authored instructions.
            proof = self.inbox.proof(scope)
            if proof is None:
                return self._state(run, 'verification', 'verification_pending')
            kind, value = proof
            if kind == 'link':
                parsed = urlsplit(value)
                if not p.accepts(value) or parsed.path != p.link_path or not re.fullmatch(r'token=[A-Za-z0-9_-]{16,128}', parsed.query):
                    return self._state(run, 'verification', 'verification_link_rejected')
                page.goto(value, wait_until='networkidle')
                self.gates[rid]['secrets'] |= {parsed.query.split('=', 1)[1]}
            elif kind == 'code' and re.fullmatch(r'[0-9]{6}', value):
                code_name = page.locator(p.code_selector).get_attribute('name') or ''
                if not code_name:
                    return self._state(run, 'verification', 'verification_outcome_unverified')
                self._open_phase(rid, 'verify', {code_name: value}, (value,))
                page.locator(p.code_selector).fill(value)
                try:
                    clicked = self._click_nav(page, p.verify_selector)
                finally:
                    self._close_phase(rid, keep_secrets=True)
                    settled = self._settle(rid, page)
                if self.gates[rid]['redirected']:
                    return self._state(run, 'unknown', 'gated_request_redirected')
                if clicked is None or not settled:
                    return self._state(run, 'verification', 'verification_outcome_unverified')
            else:
                return self._state(run, 'verification', 'verification_proof_rejected')
            return self._readback(run, p, page)
        except Exception:
            return self._state(run, 'verification', 'verification_unavailable')
        finally:
            proof = None

    def _readback(self, run, p, page):
        if not p.accepts(page.url):
            return self._state(run, 'unknown', 'origin_changed')
        for reason, selector in p.challenge_selectors:
            if page.locator(selector).count():
                return self._state(run, 'stopped', reason)
        if page.locator(p.account_email_selector).count() == 1 and page.locator(p.account_name_selector).count() == 1:
            if page.locator(p.account_email_selector).inner_text() == run['email'] and page.locator(p.account_name_selector).inner_text() == run['name']:
                return self._state(run, 'success', 'account_readback_verified')
            return self._state(run, 'unknown', 'account_identity_mismatch')
        if page.locator(p.code_selector).count():
            return self._state(run, 'verification', 'code_not_accepted')
        return self._state(run, 'partial', 'account_not_verified')

    def resend(self, owner, rid):
        run, p, page = self._load(owner, rid)
        if run['state'] != 'verification':
            return dict(run)
        try:
            run['verification_since'] = time.time()
            run['generation'] += 1
            self.store.save(run)
            self._open_phase(rid, 'resend', {})
            try:
                clicked = self._click_nav(page, p.resend_selector)
            finally:
                self._close_phase(rid, keep_secrets=True)
                settled = self._settle(rid, page)
            if self.gates[rid]['redirected']:
                return self._state(run, 'unknown', 'gated_request_redirected')
            if clicked is None or not settled:
                return self._state(run, 'verification', 'resend_outcome_unverified')
            return self._state(run, 'verification', 'code_resent')
        except Exception:
            return self._state(run, 'verification', 'resend_unavailable')

    def cancel(self, owner, rid):
        self._thread()
        run = self.store.get(owner, rid)
        if run['state'] == 'success':
            return dict(run)  # Cancellation is not account deletion.
        if rid in self.sessions:
            self.sessions.pop(rid)[0].close()
        return self._state(run, 'cancelled', 'local_work_cancelled_account_may_remain')

    # Non-route channels are removed from every frame before page scripts run. WebSocket
    # is additionally intercepted by route_web_socket (the real control; this is backup).
    CHANNEL_BLOCK_JS = """(() => {
      const dead = (name) => { try { Object.defineProperty(window, name, {
        value: undefined, writable: false, configurable: false }); } catch (e) {} };
      for (const n of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel',
                       'WebTransport', 'EventSource', 'RTCSessionDescription'])
        dead(n);
      for (const n of ['Worker', 'SharedWorker', 'Worklet', 'AudioWorklet', 'PaintWorklet'])
        dead(n);
      try { URL.createObjectURL = () => { throw new Error('blocked'); }; } catch (e) {}
      try { Object.defineProperty(navigator, 'sendBeacon', {
        value: () => false, writable: false, configurable: false }); } catch (e) {}
    })();"""

    @staticmethod
    def _review_html(p, run, seen):
        """Trusted redraw. No page text, attribute, style, pseudo-element or shadow content is
        ever copied: every string comes from the reviewed profile or the stored run, and the
        page only contributes booleans and exact-equality checks."""
        e = html.escape
        def row(label, value, note=''):
            return ('<tr><td class="k">'+e(label)+'</td><td>'+e(value)+'</td><td class="n">'
                    +e(note)+'</td></tr>')
        policy_ok = seen['policy'] == p.expected_policy
        rows = [
            row('Site', run['site']), row('Origin', run['origin']), row('State', run['state']),
            row('Reason', run['reason'] or '-'),
            row('Policy (reviewed text)', p.expected_policy if policy_ok else '[page text differs, not shown]',
                'matches page' if policy_ok else 'MISMATCH'),
            row('Account email', run['email'],
                'page shows this value' if seen['email'] == run['email'] else
                ('page value differs or absent' if seen['email_present'] else 'not shown by page')),
            row('Account name', run['name'],
                'page shows this value' if seen['name'] == run['name'] else
                ('page value differs or absent' if seen['name_present'] else 'not shown by page')),
            row('Password field', '[never captured]'), row('Code field', '[never captured]'),
        ]
        for label, key in (('Create button', 'submit'), ('Verify button', 'verify'), ('Resend button', 'resend')):
            rows.append(row(label, 'present' if seen[key] else 'absent'))
        return ('<!doctype html><html><head><meta charset="utf-8">'
                '<style>body{font:16px system-ui;background:#eef2f7;color:#14213d;margin:24px}'
                'table{border-collapse:collapse;background:#fff}td{border:1px solid #c8d0dc;padding:8px 12px}'
                '.k{font-weight:600}.n{color:#5a6475}</style></head><body>'
                '<h1>Signup review (redrawn from reviewed data)</h1><table>'+''.join(rows)+'</table></body></html>')

    def screenshot(self, owner, rid, path):
        """Redraw, never mask. The live page is only asked yes/no questions; its text is not
        rendered, so script-chosen text (spans, pseudo-elements, shadow DOM, encodings) has no
        route into the image."""
        self._thread()
        run = self.store.get(owner, rid)
        page = self.sessions[rid][1]
        profile = self.profiles[run['site']]
        # Never leave passwords or one-time codes in the live fields either.
        for selector in (profile.password_selector, profile.code_selector):
            page.locator(selector).evaluate_all('(els)=>els.forEach(e=>e.value="")')
        def text_of(selector):
            loc = page.locator(selector)
            return (loc.first.text_content() or '') if loc.count() else None
        seen = {'policy': text_of(profile.policy_selector)}
        for key, sel in (('email', profile.account_email_selector), ('name', profile.account_name_selector)):
            seen[key] = text_of(sel)
            seen[key+'_present'] = seen[key] is not None
        for key, sel in (('submit', profile.submit_selector), ('verify', profile.verify_selector),
                         ('resend', profile.resend_selector)):
            seen[key] = page.locator(sel).count() > 0
        doc = self._review_html(profile, run, seen)
        shot = self.browser.new_context(java_script_enabled=False, service_workers='block',
                                        accept_downloads=False)
        try:
            shot.route('**/*', lambda route: route.abort())
            view = shot.new_page()
            view.set_content(doc)
            view.screenshot(path=str(path), full_page=True)
        finally:
            shot.close()

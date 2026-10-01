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

    def __post_init__(self):
        p = urlsplit(self.origin)
        # Deliberate shipping boundary. No remote signup is enabled in this release.
        if p.scheme != 'http' or p.hostname != '127.0.0.1' or not p.port or p.path or p.query or p.fragment or p.username:
            raise ValueError('local fixture origin must be http://127.0.0.1:PORT')
        if not re.fullmatch(r'/[A-Za-z0-9/_-]*', self.signup_path):
            raise ValueError('invalid signup path')
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
    def __init__(self, store, vault, inbox, profiles, executable='/usr/bin/google-chrome'):
        from playwright.sync_api import sync_playwright
        self.store, self.vault, self.inbox = store, vault, inbox
        self.profiles = {p.site: p for p in profiles}
        self.thread = threading.get_ident()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=executable, headless=True,
                                              args=['--no-sandbox'])
        self.sessions = {}
        self.gates = {}
        # Restart never silently resumes or retries a possibly committed action.
        with store.db() as db:
            rows = db.execute('SELECT body FROM signup_runs').fetchall()
        for row in rows:
            run = json.loads(row[0])
            if run['state'] in {'inspection', 'ready', 'verification'}:
                self._state(run, 'unknown', 'process_restart_requires_reconciliation')

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
        if u.query and u.path != '/verify-link':
            return False
        if request.method in ('GET', 'HEAD'):
            return True
        if request.method != 'POST' or not request.is_navigation_request() or gate['used']:
            return False
        expected = gate['expected']
        if expected is None or gate['phase'] == 'idle':
            return False
        phase_path = {'submit': p.signup_path, 'verify': '/verify', 'resend': '/resend'}[gate['phase']]
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
        gate.update(phase=phase, expected=expected, used=False)
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
        context = self.browser.new_context(service_workers='block', accept_downloads=False)
        # All resources and redirects are bound to this explicit local origin, and the
        # account-creation POST is allowed exactly once: transparent browser retries
        # after a reset are aborted so one request can never create two submissions.
        # Phase gate: no request that changes server state is allowed until the actor
        # itself opens a phase (after stored approval) and the body equals exactly the
        # approved values. Page scripts never open a phase.
        gate = {'phase': 'idle', 'expected': None, 'used': False, 'secrets': set()}
        self.gates[run['id']] = gate
        def guard(route):
            request = route.request
            if not p.accepts(request.url) or not self._request_allowed(gate, p, request):
                return route.abort()
            return route.continue_()
        context.route('**/*', guard)
        # context.route does not see WebSockets. Every socket is intercepted at the browser
        # level by a handler that never calls connect_to_server, so the page talks to a
        # mock and no connection or frame ever reaches a real server.
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
            if nav is None or nav.status >= 400:
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
                 'subject': p.subject, 'since': run['verification_since'],
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
                if not p.accepts(value) or parsed.path != '/verify-link' or not re.fullmatch(r'token=[A-Za-z0-9_-]{16,128}', parsed.query):
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
                if clicked is None:
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
            if clicked is None:
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

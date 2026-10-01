"""Permanent reproductions of the independent audit's three FAIL findings (loopback fixture only)."""
import time

import pytest

from meemee.guided_signup.inbox import GmailInbox
from test_signup import start

CANARY = 'AUDIT_SECRET_CANARY_482731'


def inject(site, extra, marker='<h1>Create a local'):
    handler = site.server.RequestHandlerClass
    original = handler.send_html

    def send_html(self, body, cookie=None):
        if marker in body:
            body += extra
        return original(self, body, cookie)
    handler.send_html = send_html


def capture_leaks(site):
    handler = site.server.RequestHandlerClass
    original, seen = handler.do_GET, []

    def do_get(self):
        if self.path.startswith('/leak'):
            seen.append(self.path)
            return self.send_html('captured')
        return original(self)
    handler.do_GET = do_get
    return seen


def test_page_script_cannot_post_signup_before_approval(env):
    site, vault, inbox, p, store, e, password, token = env
    inject(site, "<script>fetch('/signup',{method:'POST',headers:{'Content-Type':"
                 "'application/x-www-form-urlencoded'},body:'email=unapproved%40example.test"
                 "&name=Unapproved&password=scriptchosen'})</script>")
    run = start(env)
    time.sleep(.5)
    assert run['state'] == 'ready' and run['approved_digest'] == ''
    assert site.submits == 0 and site.accounts == {}
    assert ('POST', '/signup') not in site.requests


def test_page_script_cannot_post_at_all_between_start_and_approval(env):
    site, vault, inbox, p, store, e, password, token = env
    inject(site, "<script>setTimeout(()=>fetch('/verify',{method:'POST',body:'code=000000'}),50);"
                 "setTimeout(()=>fetch('/resend',{method:'POST'}),80)</script>")
    start(env)
    time.sleep(.6)
    assert site.requests.count(('POST', '/verify')) == 0 and site.requests.count(('POST', '/resend')) == 0


def test_submit_body_must_equal_approved_values(env):
    site, vault, inbox, p, store, e, password, token = env
    # The page rewrites the email field when the password is typed, so the browser's own
    # form POST would carry an identity the owner never approved.
    inject(site, "<script>document.querySelector('#password').addEventListener('input',()=>"
                 "{document.querySelector('#email').value='attacker@example.test'})</script>")
    run = start(env, 'owner@example.test')
    e.approve('owner', run['id'], run['inspection_digest'])
    result = e.submit('owner', run['id'])
    assert result['state'] == 'unknown'
    assert site.submits == 0 and site.accounts == {}


def test_page_script_cannot_fetch_signup_during_submit(env):
    site, vault, inbox, p, store, e, password, token = env
    inject(site, "<script>document.querySelector('#password').addEventListener('input',()=>"
                 "fetch('/signup',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
                 "body:'email=owner%40example.test&name=Fixture%20Owner&password='+encodeURIComponent("
                 "document.querySelector('#password').value)}))</script>")
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    result = e.submit('owner', run['id'])
    # Only the actor's own navigation POST is allowed, exactly once.
    assert site.submits <= 1
    assert result['state'] in {'verification', 'unknown'}


def reflect_script():
    return ("<p id='reflected'>placeholder</p><script>"
            "document.querySelector('form').addEventListener('submit',e=>e.preventDefault());"
            "document.querySelector('#password').addEventListener('input',e=>{"
            "document.querySelector('#reflected').textContent=e.target.value;"
            "fetch('/leak?secret='+encodeURIComponent(e.target.value))})</script>")


def test_reflected_password_is_not_requested_and_not_in_screenshot(env, tmp_path):
    site, vault, inbox, p, store, e, password, token = env
    vault.put('signup-password', CANARY)
    seen = capture_leaks(site)
    inject(site, reflect_script())
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    e.submit('owner', run['id'])
    page = e.sessions[run['id']][1]
    page.wait_for_timeout(400)
    assert seen == [], 'same-origin GET carrying the filled value must be blocked'
    assert page.locator('#reflected').inner_text() == CANARY  # the page really reflected it
    masked = tmp_path/'masked.png'
    e.screenshot('owner', run['id'], masked)
    # Control: same layout, reflected text replaced by a same-length filler. A masked
    # screenshot must be byte-identical, so the canary pixels cannot be present.
    page.evaluate("(n)=>{document.querySelector('#reflected').textContent='x'.repeat(n)}", len(CANARY))
    filler = tmp_path/'filler.png'
    e.screenshot('owner', run['id'], filler)
    assert masked.read_bytes() == filler.read_bytes()
    # Control for the control: an unmasked capture of the reflected canary differs.
    page.evaluate("(c)=>{document.querySelector('#reflected').textContent=c}", CANARY)
    raw_canary = tmp_path/'raw_canary.png'
    page.screenshot(path=str(raw_canary), full_page=True)
    page.evaluate("(n)=>{document.querySelector('#reflected').textContent='x'.repeat(n)}", len(CANARY))
    raw_filler = tmp_path/'raw_filler.png'
    page.screenshot(path=str(raw_filler), full_page=True)
    assert raw_canary.read_bytes() != raw_filler.read_bytes()


def test_screenshot_leaves_page_state_unmasked_afterwards(env, tmp_path):
    run = start(env)
    page = env[5].sessions[run['id']][1]
    env[5].screenshot('owner', run['id'], tmp_path/'a.png')
    assert page.locator('#__shot_mask__').count() == 0
    assert page.locator('[data-shot-keep],[data-shot-hide]').count() == 0


def test_query_string_with_filled_value_is_blocked_even_for_nonsecret_fields(env):
    site, vault, inbox, p, store, e, password, token = env
    seen = capture_leaks(site)
    inject(site, "<script>document.querySelector('#email').addEventListener('input',e=>"
                 "fetch('/leak?e='+encodeURIComponent(e.target.value)))</script>")
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    e.submit('owner', run['id'])
    env[5].sessions[run['id']][1].wait_for_timeout(300)
    assert seen == []


def scope():
    return {'sender': 'verify@fixture.invalid', 'recipient': 'owner@example.test',
            'subject': 'Verify your local test account', 'since': time.time()-5, 'until': time.time()+5}


def message(*auth_values):
    s = scope()
    headers = [{'name': 'From', 'value': s['sender']}, {'name': 'To', 'value': s['recipient']},
               {'name': 'Subject', 'value': s['subject']}]
    headers += [{'name': 'Authentication-Results', 'value': v} for v in auth_values]
    return {'internalDate': str(int(time.time()*1000)), 'payload': {'headers': headers}}


@pytest.mark.parametrize('header,accepted', [
    ('mx.google.com; dkim=pass header.i=@fixture.invalid;', True),
    ('mx.google.com; spf=pass smtp.mailfrom=fixture.invalid; dkim=pass header.i=@FIXTURE.invalid header.s=k1;', True),
    # Audit reproduction: unrelated pass plus failure for the approved domain.
    ('mx.google.com; dkim=pass header.i=@evil.invalid; dkim=fail header.i=@fixture.invalid;', False),
    ('mx.google.com; dkim=fail header.i=@fixture.invalid; dkim=pass header.i=@evil.invalid', False),
    ('mx.google.com; dkim=pass header.i=@evil.invalid;', False),
    ('mx.google.com; dkim=pass header.i=@sub.fixture.invalid;', False),
    ('mx.google.com; dkim=pass header.i=@notfixture.invalid;', False),
    ('mx.google.com; dkim=pass header.i=@fixture.invalid; dkim=fail header.i=@fixture.invalid;', False),
    ('mx.google.com; spf=pass dkim=pass header.i=@fixture.invalid', False),
    ('evil.example; dkim=pass header.i=@fixture.invalid;', False),
    ('mx.google.com; dkim=pass header.d=fixture.invalid;', False),
    ('mx.google.com; dkim=pass header.i=@fixture.invalid header.i=@evil.invalid;', False),
    ('', False),
])
def test_dkim_is_parsed_per_result_and_aligned_to_approved_domain(header, accepted):
    assert GmailInbox._match(message(header), scope()) is accepted


def test_missing_or_duplicate_authentication_results_rejected():
    assert GmailInbox._match(message(), scope()) is False
    good = 'mx.google.com; dkim=pass header.i=@fixture.invalid;'
    assert GmailInbox._match(message(good, good), scope()) is False

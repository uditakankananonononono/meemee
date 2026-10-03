import pytest
from meemee.guided_signup.engine import Profile, Engine
from meemee.guided_signup.inbox import GmailInbox


def test_https_reviewed_host_profile_accepted_and_scoped():
    p = Profile(site='x', origin='https://example.org', dkim_domains=('mail.example.org', 'example.org'))
    assert p.accepts('https://example.org/signup')
    for url in ('http://example.org/', 'https://evil.example.org/', 'https://example.org.evil.test/',
                'https://example.org:8443/', 'https://u@example.org/'):
        assert not p.accepts(url)


@pytest.mark.parametrize('origin', [
    'http://example.org', 'https://example.org:8443', 'https://10.0.0.1', 'https://localhost',
    'https://example.org/path', 'https://user@example.org', 'https://*.example.org', 'https://EXAMPLE.org',
    'http://127.0.0.1', 'https://127.0.0.1:9', 'https://example.org?x=1'])
def test_bad_origins_rejected(origin):
    with pytest.raises(ValueError):
        Profile(site='x', origin=origin)


@pytest.mark.parametrize('d', ['*.example.org', '.example.org', 'example', 'Example.org', 'a b.org'])
def test_bad_dkim_domain_rejected(d):
    with pytest.raises(ValueError):
        Profile(site='x', origin='https://example.org', dkim_domains=(d,))


def test_dkim_exact_list_not_suffix_or_foreign():
    ok = 'mx.google.com; dkim=pass header.i=@mail.example.org header.s=s1'
    assert GmailInbox._aligned_dkim(ok, ('mail.example.org', 'example.org'))
    assert not GmailInbox._aligned_dkim(ok, ('example.org',))                    # no suffix/parent match
    assert not GmailInbox._aligned_dkim('mx.google.com; dkim=pass header.i=@sendgrid.net', ('example.org',))
    assert not GmailInbox._aligned_dkim(ok + '; dkim=fail header.i=@example.org', ('mail.example.org', 'example.org'))
    assert GmailInbox._aligned_dkim(ok, 'mail.example.org')                      # old str form still works


def test_launch_args_https_profiles_get_no_bypass_for_real_host():
    args = Engine._launch_args([Profile(site='x', origin='https://example.org')])
    joined = ' '.join(args)
    assert 'example.org' not in joined and 'MAP * ~NOTFOUND' in joined


def test_phase_paths_and_user_agent_validated():
    p = Profile(site='x', origin='https://example.org', verify_path='/account/verify', resend_path='/account/resend',
                link_path='/account/confirm', user_agent='AssistantBot/1.0 (automated; owner contact: owner@example.org)')
    assert p.link_path == '/account/confirm'
    for bad in ({'verify_path': 'verify'}, {'link_path': '/a?b=1'}, {'resend_path': '/a b'},
                {'user_agent': 'x\r\nHost: evil'}, {'user_agent': 'x' * 201}):
        with pytest.raises(ValueError):
            Profile(site='x', origin='https://example.org', **bad)

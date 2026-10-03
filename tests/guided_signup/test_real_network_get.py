"""Opt-in (ATLAS_REAL_NET=1): production path, no test_origin_map, real DNS + TLS to the IANA documentation
domain, GET only. It has no signup form, so the run must STOP; nothing is submitted."""
import os
import secrets

import pytest

from meemee.guided_signup import Engine, Profile, Store
from meemee.guided_signup.vault import LocalVault

pytestmark = pytest.mark.skipif(not os.getenv('ATLAS_REAL_NET'), reason='needs internet; set ATLAS_REAL_NET=1')


def test_real_dns_tls_get_stops_without_form(tmp_path):
    v = LocalVault(tmp_path / 'v.sqlite', LocalVault.key())
    v.put('signup-password', secrets.token_urlsafe(24))
    p = Profile('iana', 'https://example.org', signup_path='/', user_agent='AutomatedAssistant/1.0 (transport test, GET only)')
    e = Engine(Store(tmp_path / 'r.sqlite'), v, None, [p])
    try:
        run = e.start('owner', 'iana', 'owner@example.test', 'N', 'signup-password')
        _, page = e.sessions[run['id']]
        assert run['state'] == 'stopped' and page.title() == 'Example Domain'
        assert page.evaluate('navigator.userAgent') == p.user_agent
        assert page.evaluate("fetch('https://www.iana.org/').then(()=>'fetched').catch(()=>'blocked')") == 'blocked'
    finally:
        e.close()


def test_real_dns_names_resolving_to_private_space_are_refused():
    from meemee.guided_signup import transport
    for name in ('localtest.me', '169.254.169.254.nip.io', '127.0.0.1.nip.io'):
        with pytest.raises(transport.TransportError):
            transport.fetch('GET', f'https://{name}/', {})

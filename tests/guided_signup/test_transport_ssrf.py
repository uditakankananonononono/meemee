import socket
import ssl

import pytest

from meemee.guided_signup import transport as t


def resolver_for(*addrs):
    calls = []
    def r(host, port, type=0):
        calls.append(host)
        return [(socket.AF_INET6 if ':' in a else socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port) + ((0, 0) if ':' in a else ())) for a in addrs]
    r.calls = calls
    return r


@pytest.mark.parametrize('addr', [
    '127.0.0.1', '127.1.2.3', '::1', '10.0.0.5', '172.16.0.1', '192.168.1.1', '169.254.169.254', '100.64.0.1',
    '0.0.0.0', '224.0.0.1', 'fe80::1', 'fc00::1', 'fd00::5', '::ffff:127.0.0.1', '::ffff:169.254.169.254',
    '2002:7f00:1::', '::', '192.0.2.1', '198.51.100.7', '255.255.255.255'])
def test_non_public_addresses_refused(addr):
    with pytest.raises(t.TransportError):
        t.fetch('GET', 'https://example.org/', {}, resolver=resolver_for(addr))


def test_one_private_record_refuses_all_even_with_public_first():
    with pytest.raises(t.TransportError):
        t.fetch('GET', 'https://example.org/', {}, resolver=resolver_for('93.184.216.34', '169.254.169.254'))


def test_public_addresses_pass_validation_and_resolution_happens_once():
    r = resolver_for('93.184.216.34', '2606:2800:21f:cb07:6820:80da:af6b:8b2c')
    got = t.pinned_addresses('example.org', 443, r)
    assert len(got) == 2 and r.calls == ['example.org']


@pytest.mark.parametrize('url', ['http://example.org/', 'https://example.org:8443/', 'https://u@example.org/',
                                 'ftp://example.org/', 'https:///x'])
def test_scheme_port_userinfo_refused(url):
    with pytest.raises(t.TransportError):
        t.fetch('GET', url, {}, resolver=resolver_for('93.184.216.34'))


def test_unresolvable_name_refused():
    def r(*a, **k):
        raise socket.gaierror('nope')
    with pytest.raises(t.TransportError):
        t.fetch('GET', 'https://example.org/', {}, resolver=r)


def test_connection_uses_pinned_validated_address_not_a_second_lookup(monkeypatch):
    """Resolver answers public once; if the code looked the name up again (rebinding) it would see 127.0.0.1."""
    answers = iter([['93.184.216.34'], ['127.0.0.1']])
    def r(host, port, type=0):
        a = next(answers)[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, port))]
    dialed = []
    class Boom(Exception): pass
    class FakeSock:
        def __init__(self, *a): pass
        def settimeout(self, x): pass
        def connect(self, addr): dialed.append(addr); raise OSError('stop')
        def close(self): pass
    monkeypatch.setattr(t.socket, 'socket', FakeSock)
    with pytest.raises(t.TransportError):
        t.fetch('GET', 'https://example.org/', {}, resolver=r)
    assert dialed == [('93.184.216.34', 443)]


def test_tls_hostname_mismatch_fails(tmp_path):
    """Pinned local TLS server with a cert for a different name must fail verification (not disabled)."""
    import subprocess, threading
    d = tmp_path
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'k.pem', '-out', 'c.pem', '-days', '2',
                    '-subj', '/CN=other.example.net', '-addext', 'subjectAltName=DNS:other.example.net'], cwd=d, check=True, capture_output=True)
    srv = socket.socket(); srv.bind(('127.0.0.1', 0)); srv.listen(1)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(d / 'c.pem', d / 'k.pem')
    def serve():
        try:
            c, _ = srv.accept(); ctx.wrap_socket(c, server_side=True)
        except Exception:
            pass
    threading.Thread(target=serve, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=str(d / 'c.pem'))        # CA trusted, name still wrong
    with pytest.raises(t.TransportError):
        t.fetch('GET', 'https://signup.test.example.org/', {}, pin=('127.0.0.1', srv.getsockname()[1]), context=client_ctx)
    srv.close()


def test_reviewed_https_profile_never_uses_route_fetch():
    import inspect
    from meemee.guided_signup.engine import Engine
    src = inspect.getsource(Engine._respond)
    assert src.count('route.fetch') == 1 and "u.hostname == '127.0.0.1'" in src.split('route.fetch')[0]

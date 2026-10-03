"""SSRF-safe HTTPS fetch for reviewed real hosts.

Playwright's own fetch (route.fetch) resolves names with the driver's resolver and ignores Chrome's DNS
and proxy lock, so it must never carry a real host. This module does the whole job itself:
resolve ONCE, require every returned address to be globally routable, connect to a pinned validated
address (no second resolution, so no rebinding window), verify TLS against the reviewed host name
(SNI + certificate; verification is never disabled), send the reviewed Host header, no proxy, no
redirects (the caller decides), bounded size and time."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

MAX_BYTES = 2_000_000
HOP = {'connection', 'keep-alive', 'proxy-connection', 'te', 'trailer', 'transfer-encoding', 'upgrade',
       'host', 'content-length', 'accept-encoding'}


class TransportError(Exception):
    pass


def public(address):
    ip = ipaddress.ip_address(address.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour:
        ip = ip.sixtofour
    return ip.is_global and not ip.is_multicast


def pinned_addresses(host, port, resolver=socket.getaddrinfo):
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise TransportError('name did not resolve')
    addresses = [(i[0], i[4]) for i in infos]
    if not addresses or not all(public(a[1][0]) for a in addresses):
        raise TransportError('host resolves to a non-public address')      # one bad record refuses all
    return addresses


def fetch(method, url, headers, body=None, *, resolver=socket.getaddrinfo, pin=None, timeout=10,
          max_bytes=MAX_BYTES, context=None):
    """Returns (status, [(name, value)], bytes). `pin=(ip, port)` is the test-only seam (explicit address,
    still TLS-verified against the reviewed host); production callers never pass it."""
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.port not in (None, 443) or u.username:
        raise TransportError('only https on the default port')
    host = u.hostname
    deadline = time.monotonic() + timeout
    if pin is not None:
        targets = [(socket.AF_INET, pin)]
    else:
        targets = pinned_addresses(host, 443, resolver)
    ctx = context or ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    last = None
    for family, addr in targets:
        try:
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(max(0.1, deadline - time.monotonic()))
            raw.connect(addr)
            sock = ctx.wrap_socket(raw, server_hostname=host)
            break
        except (OSError, ssl.SSLError) as e:
            last = e
            raw.close()
    else:
        raise TransportError('connection or TLS verification failed')
    try:
        conn = http.client.HTTPSConnection(host, 443, timeout=max(0.1, deadline - time.monotonic()))
        conn.sock = sock
        path = (u.path or '/') + ('?' + u.query if u.query else '')
        out = {k: v for k, v in headers.items() if k.lower() not in HOP}
        out.update({'Host': u.netloc, 'Accept-Encoding': 'identity', 'Connection': 'close'})
        conn.request(method, path, body=body, headers=out)
        response = conn.getresponse()
        data = b''
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > max_bytes or time.monotonic() > deadline:
                raise TransportError('response too large or too slow')
        return response.status, response.getheaders(), data
    except (OSError, http.client.HTTPException):
        raise TransportError('request failed')
    finally:
        sock.close()

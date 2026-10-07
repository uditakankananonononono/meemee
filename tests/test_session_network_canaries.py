"""Real Chromium session requests must obey policy beyond initial navigation."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from meemee.browser_sessions import BrowserSessionError, BrowserSessionManager, BrowserSessionStore
from meemee.tools.browser_session import OpenArgs


@pytest.mark.parametrize('profile',['.','..'])
def test_session_profile_rejects_parent_directory(profile):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OpenArgs(url='https://example.com',profile=profile)


@pytest.mark.parametrize('kind,domains', [('resource',[]),('resource',['127.0.0.1']),('redirect',['127.0.0.1'])])
def test_live_session_never_requests_forbidden_target(tmp_path, monkeypatch, kind, domains):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/private':
                hits.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location',f'http://localhost:{self.server.server_port}/private')
                self.end_headers()
            else:
                body = (f'<html><body>Public<img src="http://localhost:{self.server.server_port}/private"></body></html>'
                    if self.path == '/resource' else '<html><body>Private</body></html>').encode()
                self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    def fixture_policy(url,*args):
        if urlparse(url).hostname == 'localhost':
            raise BrowserSessionError('private fixture destination')
        return url
    monkeypatch.setattr('meemee.browser_sessions.check_url',fixture_policy)
    manager = BrowserSessionManager(BrowserSessionStore(tmp_path/'sessions.db'),allow_private_hosts=True)
    try:
        asyncio.run(manager.open(f'http://127.0.0.1:{server.server_port}/{kind}', allowed_domains=domains))
        assert hits == [], f'forbidden target reached: {hits}'
    finally:
        manager.shutdown(); server.shutdown(); thread.join()

"""Real local Chromium fixtures expose redirect/subresource policy gaps."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meemee.tools.browser import BrowseArgs, BrowserNavigate


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['redirect','resource'])
async def test_browser_rechecks_requests_before_private_target(tmp_path, monkeypatch, kind):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/private':
                hits.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', f'http://localhost:{self.server.server_port}/private')
                self.end_headers()
            else:
                body = (f'<html><body>Public<img src="http://localhost:{self.server.server_port}/private"></body></html>'
                        if self.path == '/resource' else '<html><body>Private</body></html>').encode()
                self.send_response(200)
                self.send_header('Content-Type','text/html')
                self.end_headers()
                self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def public_fixture(url):
        from urllib.parse import urlparse
        if urlparse(url).hostname == 'localhost':
            raise ValueError('private fixture target')
        return url
    monkeypatch.setattr('meemee.tools.browser.validate_public_url', public_fixture)
    try:
        try:
            await BrowserNavigate(tmp_path).run(BrowseArgs(
                url=f'http://127.0.0.1:{server.server_port}/{kind}', wait_ms=100))
        except (ValueError, Exception) as exc:
            # Navigation abort is allowed; assertions prove no forbidden request.
            if 'Executable doesn' in str(exc) or 'Host system is missing' in str(exc):
                raise
        assert hits == [], f'private target was requested by {kind}: {hits}'
    finally:
        server.shutdown()
        thread.join()

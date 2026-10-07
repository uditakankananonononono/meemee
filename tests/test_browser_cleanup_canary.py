"""Drive real browser tool logic with a context contract, not real Chromium."""
import sys
from types import SimpleNamespace

import pytest

from meemee.tools.browser import BrowseArgs, BrowserNavigate


class BrowserContract:
    closed = False
    async def launch(self, **kwargs):
        return self
    async def new_context(self, **kwargs):
        return self
    async def new_page(self):
        return self
    def on(self, *args):
        pass
    async def route(self, *args, **kwargs):
        pass
    async def goto(self, *args, **kwargs):
        raise RuntimeError('navigation crashed')
    async def close(self):
        self.closed = True
    async def __aenter__(self):
        return SimpleNamespace(chromium=self)
    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_browser_failure_closes_context_before_return(tmp_path, monkeypatch):
    browser = BrowserContract()
    monkeypatch.setitem(sys.modules, 'playwright', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'playwright.async_api', SimpleNamespace(async_playwright=lambda:browser))
    monkeypatch.setattr('meemee.tools.browser.validate_public_url', lambda url:url)
    with pytest.raises(RuntimeError, match='navigation crashed'):
        await BrowserNavigate(tmp_path).run(BrowseArgs(url='https://example.com'))
    assert browser.closed, 'navigation failure left browser context open'

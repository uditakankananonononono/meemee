"""Area 217: browser request-guard containment.

LIMIT, stated verbatim: no real Chromium and no playwright package are available here, so these tests
drive the route handlers with fake route/request/page objects and a monkeypatched getaddrinfo. They
show handler logic only, NOT end-to-end browser behavior. Playwright error classes are matched by
module/name string, not isinstance; test env has no playwright package; not verified against the real
classes. Tests marked PROTECTION already pass on base.
"""
import asyncio
import socket
import sys
import time
import types

import pytest

from meemee.browser_sessions import (
    BrowserSessionError,
    BrowserSessionManager,
    BrowserSessionStore,
    check_url,
)
from meemee.tools.browser import BrowseArgs, BrowserNavigate, validate_public_url

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

PWError = type("Error", (Exception,), {"__module__": "playwright._impl._errors"})
PWTimeout = type("TimeoutError", (PWError,), {"__module__": "playwright._impl._errors"})
FakeNotPlaywright = type("Error", (Exception,), {"__module__": "evilpkg.errors"})


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeRoute:
    def __init__(self, url, fetch_exc=None):
        self.request, self.fetch_exc, self.aborted, self.fulfilled = FakeRequest(url), fetch_exc, None, False

    async def fetch(self, **kw):
        if self.fetch_exc:
            raise self.fetch_exc
        return types.SimpleNamespace(status=200, headers={})

    async def fulfill(self, response=None):
        self.fulfilled = True

    async def abort(self, reason):
        self.aborted = reason


class FakePage:
    url = "https://example.test/"

    def __init__(self):
        self.handlers = {}

    def on(self, *a):
        pass

    async def goto(self, *a, **k):
        return types.SimpleNamespace(status=200)

    async def wait_for_timeout(self, ms):
        pass

    def locator(self, sel):
        return types.SimpleNamespace(inner_text=self._text)

    async def _text(self):
        return "hello"

    async def title(self):
        return "t"


class FakeContext:
    def __init__(self):
        self.handler, self.pages = None, []
        self.page = FakePage()

    async def route(self, pattern, handler):
        self.handler = handler

    async def new_page(self):
        return self.page

    async def close(self):
        pass


def install_fake_playwright(monkeypatch, ctx):
    class Browser:
        async def new_context(self, **k):
            return ctx

        async def close(self):
            pass

    class PW:
        chromium = types.SimpleNamespace(launch=lambda **k: _async(Browser()))

    async def _async(v):
        return v

    class CM:
        async def __aenter__(self):
            return PW()

        async def __aexit__(self, *a):
            return False
    mod = types.ModuleType("playwright.async_api")
    mod.async_playwright = lambda: CM()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", mod)


async def tool_handler(monkeypatch, tmp_path):
    ctx = FakeContext()
    install_fake_playwright(monkeypatch, ctx)
    tool = BrowserNavigate(tmp_path)
    await tool.run(BrowseArgs(url="https://example.test/"))
    assert ctx.handler is not None
    return ctx.handler


async def session_handler(monkeypatch, tmp_path):
    ctx = FakeContext()
    store = BrowserSessionStore(tmp_path / "s.db")
    mgr = BrowserSessionManager(store)

    async def ensure():
        return types.SimpleNamespace(new_context=lambda **k: _ctx(ctx))

    async def _ctx(c):
        return c

    async def state(live, **k):
        return {"challenge": {"detected": False, "markers": [], "frames": []}}
    monkeypatch.setattr(mgr, "_ensure_browser", ensure)
    monkeypatch.setattr(mgr, "_state", state)
    await mgr._open("https://example.test/", "u", [], None, False)
    assert ctx.handler is not None
    return ctx.handler, store


# ---- item 1: DNS failure contained in the tool's validation contract
@pytest.mark.parametrize("exc", [socket.gaierror(-2, "Name or service not known"), UnicodeError("label too long")])
def test_validate_public_url_dns_failure_is_fixed_text_valueerror(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ValueError) as ei:
        validate_public_url("https://secret-host.test/x")
    assert str(ei.value) == "browser URL could not be resolved"
    assert "secret-host" not in str(ei.value) and "Name or service" not in str(ei.value)


def test_check_url_idna_and_bad_port_are_session_errors(monkeypatch):
    def boom(*a, **k):
        raise UnicodeError("label too long")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(BrowserSessionError, match="cannot resolve"):
        check_url("https://x.test/", [], False)
    monkeypatch.undo()
    with pytest.raises(BrowserSessionError, match="cannot resolve"):
        check_url("https://x.test:99999999/", [], False)


# ---- item 2: playwright-class errors from route.fetch abort instead of escaping
@pytest.mark.parametrize("exc", [PWError("net::ERR"), PWTimeout("t")], ids=["Error", "TimeoutError"])
async def test_tool_handler_playwright_fetch_error_aborts(monkeypatch, tmp_path, exc):
    handler = await tool_handler(monkeypatch, tmp_path)
    route = FakeRoute("https://example.test/a", exc)
    await handler(route)
    assert route.aborted == "blockedbyclient" and not route.fulfilled


@pytest.mark.parametrize("exc", [PWError("net::ERR"), PWTimeout("t")], ids=["Error", "TimeoutError"])
async def test_session_handler_playwright_fetch_error_aborts_and_records(monkeypatch, tmp_path, exc):
    handler, store = await session_handler(monkeypatch, tmp_path)
    route = FakeRoute("https://example.test/a", exc)
    await handler(route)
    assert route.aborted == "blockedbyclient"
    kinds = [r[0] for r in store.db.execute("SELECT kind FROM browser_session_events")]
    assert "request_blocked" in kinds


@pytest.mark.parametrize("exc", [RuntimeError("bug"), FakeNotPlaywright("not playwright")], ids=["RuntimeError", "other-module-Error"])
async def test_PROTECTION_non_playwright_errors_are_not_swallowed(monkeypatch, tmp_path, exc):
    handler = await tool_handler(monkeypatch, tmp_path)
    with pytest.raises(type(exc)):
        await handler(FakeRoute("https://example.test/a", exc))
    handler2, _ = await session_handler(monkeypatch, tmp_path)
    with pytest.raises(type(exc)):
        await handler2(FakeRoute("https://example.test/a", exc))


# ---- item 3: DNS must not block the event loop
async def _ticks_during(handler, monkeypatch):
    def slow(*a, **k):
        time.sleep(0.4)
        return PUBLIC
    monkeypatch.setattr(socket, "getaddrinfo", slow)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1
    t = asyncio.create_task(ticker())
    await handler(FakeRoute("https://example.test/a"))
    t.cancel()
    return ticks


async def test_tool_handler_dns_does_not_block_event_loop(monkeypatch, tmp_path):
    handler = await tool_handler(monkeypatch, tmp_path)
    assert await _ticks_during(handler, monkeypatch) >= 5


async def test_session_handler_dns_does_not_block_event_loop(monkeypatch, tmp_path):
    handler, _ = await session_handler(monkeypatch, tmp_path)
    assert await _ticks_during(handler, monkeypatch) >= 5


# ---- PROTECTION
async def test_PROTECTION_private_resolution_still_blocked_in_handlers(monkeypatch, tmp_path):
    handler = await tool_handler(monkeypatch, tmp_path)
    handler2, _ = await session_handler(monkeypatch, tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    for h in (handler, handler2):
        route = FakeRoute("https://example.test/a")
        await h(route)
        assert route.aborted == "blockedbyclient" and not route.fulfilled


async def test_PROTECTION_public_request_is_fulfilled(monkeypatch, tmp_path):
    handler = await tool_handler(monkeypatch, tmp_path)
    route = FakeRoute("https://example.test/a")
    await handler(route)
    assert route.fulfilled and route.aborted is None

import socket

import pytest

from meemee.tools.browser import validate_public_url


def test_browser_rejects_non_http():
    with pytest.raises(ValueError):
        validate_public_url("file:///etc/passwd")


def test_browser_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ValueError, match="private"):
        validate_public_url("http://example.test")


def test_screenshot_escape(tmp_path):
    from meemee.tools.browser import BrowserNavigate
    with pytest.raises(ValueError, match="escapes"):
        BrowserNavigate(tmp_path).safe_screenshot("../shot.png")


def test_browser_domain_policy_blocks_unlisted_public_host(monkeypatch, tmp_path):
    import pytest

    from meemee.tools.browser import BrowseArgs, BrowserNavigate
    monkeypatch.setattr("socket.getaddrinfo", lambda *a: [(None,None,None,None,("8.8.8.8",443))])
    with pytest.raises(ValueError,match="domain policy"):
        import asyncio
        asyncio.run(BrowserNavigate(tmp_path).run(BrowseArgs(url="https://example.com",allowed_domains=["allowed.example"])))


def test_browser_download_and_screenshot_paths_cannot_escape(tmp_path):
    import pytest

    from meemee.tools.browser import BrowserNavigate
    browser=BrowserNavigate(tmp_path)
    with pytest.raises(ValueError,match="escapes workspace"): browser.safe_screenshot("../secret")
    assert browser.safe_screenshot("downloads") == tmp_path/"downloads"

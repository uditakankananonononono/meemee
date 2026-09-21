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

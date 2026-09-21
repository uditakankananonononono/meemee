from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class BrowseArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    wait_ms: int = Field(default=500, ge=0, le=10_000)
    screenshot_path: str | None = None


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser URL must use http or https")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("browser URL resolves to a private or reserved address")
    return url


class BrowserNavigate(Tool):
    name = "browser.navigate"
    description = "Open a public web page in Chromium and return title, final URL, and visible text."
    risk = Risk.READ
    arguments_model = BrowseArgs

    def __init__(self, workspace: Path, headless: bool = True):
        self.workspace = workspace.resolve()
        self.headless = headless

    async def run(self, arguments: BrowseArgs) -> dict[str, object]:
        url = validate_public_url(arguments.url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ValueError("browser extra is not installed; run pip install 'meemee-agent[browser]' and playwright install chromium") from exc
        screenshot = None
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            page = await browser.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(arguments.wait_ms)
            if arguments.screenshot_path:
                target = (self.workspace / arguments.screenshot_path).resolve()
                if self.workspace not in target.parents:
                    raise ValueError("screenshot path escapes workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(target), full_page=True)
                screenshot = str(target.relative_to(self.workspace))
            result = {
                "url": page.url,
                "title": await page.title(),
                "text": (await page.locator("body").inner_text())[:200_000],
                "status": response.status if response else None,
                "screenshot": screenshot,
            }
            await browser.close()
            return result

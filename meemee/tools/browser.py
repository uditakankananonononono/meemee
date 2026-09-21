from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..types import Risk
from .base import Tool


class BrowserAction(BaseModel):
    kind: Literal["click", "fill", "press", "select", "wait"]
    selector: str | None = Field(default=None, max_length=1000)
    value: str | None = Field(default=None, max_length=20_000)
    milliseconds: int | None = Field(default=None, ge=0, le=10_000)


class BrowseArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    actions: list[BrowserAction] = Field(default_factory=list, max_length=30)
    wait_ms: int = Field(default=500, ge=0, le=10_000)
    screenshot_path: str | None = None
    profile: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")


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
    description = "Open a public page in Chromium; optionally interact using locators and a persistent profile."
    risk = Risk.WRITE
    arguments_model = BrowseArgs

    def __init__(self, workspace: Path, headless: bool = True, profiles_dir: Path | None = None):
        self.workspace = workspace.resolve()
        self.headless = headless
        self.profiles_dir = (profiles_dir or workspace / ".meemee-browser").resolve()

    def safe_screenshot(self, raw: str) -> Path:
        target = (self.workspace / raw).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError("screenshot path escapes workspace")
        return target

    async def run(self, arguments: BrowseArgs) -> dict[str, object]:
        url = validate_public_url(arguments.url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ValueError("browser extra is not installed; run pip install 'meemee-agent[browser]' and playwright install chromium") from exc
        screenshot = None
        events: list[dict[str, object]] = []
        async with async_playwright() as playwright:
            if arguments.profile:
                context = await playwright.chromium.launch_persistent_context(
                    str(self.profiles_dir / arguments.profile), headless=self.headless
                )
                page = context.pages[0] if context.pages else await context.new_page()
                close = context.close
            else:
                browser = await playwright.chromium.launch(headless=self.headless)
                context = await browser.new_context()
                page = await context.new_page()
                close = browser.close
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            for index, action in enumerate(arguments.actions):
                if action.kind == "wait":
                    await page.wait_for_timeout(action.milliseconds or 0)
                else:
                    if not action.selector:
                        raise ValueError(f"action {index} requires selector")
                    locator = page.locator(action.selector).first
                    if action.kind == "click":
                        await locator.click(timeout=10_000)
                    elif action.kind == "fill":
                        await locator.fill(action.value or "", timeout=10_000)
                    elif action.kind == "press":
                        await locator.press(action.value or "Enter", timeout=10_000)
                    elif action.kind == "select":
                        await locator.select_option(action.value or "", timeout=10_000)
                events.append({"index": index, "kind": action.kind, "url": page.url})
            await page.wait_for_timeout(arguments.wait_ms)
            if arguments.screenshot_path:
                target = self.safe_screenshot(arguments.screenshot_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(target), full_page=True)
                screenshot = str(target.relative_to(self.workspace))
            result = {"url": page.url, "title": await page.title(), "text": (await page.locator("body").inner_text())[:200_000], "status": response.status if response else None, "screenshot": screenshot, "actions": events}
            await close()
            return result

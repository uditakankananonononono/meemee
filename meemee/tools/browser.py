from __future__ import annotations

import asyncio
import ipaddress
import socket
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from ..types import Risk
from .base import Tool


class BrowserAction(BaseModel):
    kind: Literal["click", "fill", "press", "select", "wait"]
    selector: str | None = Field(default=None, max_length=1000)
    value: str | None = Field(default=None, max_length=20_000)
    milliseconds: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def required_selector(self):
        if self.kind != "wait" and (not self.selector or not self.selector.strip()):
            raise ValueError(f"{self.kind} action requires selector")
        return self


class BrowseArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    actions: list[BrowserAction] = Field(default_factory=list, max_length=30)
    wait_ms: int = Field(default=500, ge=0, le=10_000)
    screenshot_path: str | None = None
    profile: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    download_dir: str | None = None

    @field_validator("profile")
    @classmethod
    def profile_directory(cls, value):
        if value in {".", ".."}:
            raise ValueError("profile must name a directory, not dot or parent")
        return value


def _is_playwright_error(exc: BaseException) -> bool:
    """Playwright error classes matched by module/name string, not isinstance; test env has no
    playwright package; not verified against the real classes."""
    return any(c.__module__.split(".")[0] == "playwright" and c.__name__ in ("Error", "TimeoutError")
               for c in type(exc).__mro__)


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser URL must use http or https")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        raise ValueError("browser URL could not be resolved") from None
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
        hostname = (urlparse(url).hostname or "").lower()
        if arguments.allowed_domains and not any(hostname == domain.lower() or hostname.endswith("." + domain.lower()) for domain in arguments.allowed_domains):
            raise ValueError("browser URL is outside the per-run domain policy")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ValueError("browser extra is not installed; run pip install 'meemee-agent[browser]' and playwright install chromium") from exc
        screenshot = None
        events: list[dict[str, object]] = []
        async with async_playwright() as playwright:
            close = None
            try:
                if arguments.profile:
                    context = await playwright.chromium.launch_persistent_context(
                        str(self.profiles_dir / arguments.profile), headless=self.headless, service_workers="block"
                    )
                    close = context.close
                    page = context.pages[0] if context.pages else await context.new_page()
                else:
                    browser = await playwright.chromium.launch(headless=self.headless)
                    close = browser.close
                    context = await browser.new_context(service_workers="block")
                    page = await context.new_page()
                blocked_requests: list[str] = []

                def allowed_request(request_url):
                    validate_public_url(request_url)
                    host = (urlparse(request_url).hostname or "").lower()
                    if arguments.allowed_domains and not any(
                        host == domain.lower() or host.endswith("." + domain.lower())
                        for domain in arguments.allowed_domains):
                        raise ValueError("request is outside per-run domain policy")

                async def guarded_request(route):
                    current = route.request.url
                    try:
                        # getaddrinfo blocks: keep it off the event loop
                        await asyncio.to_thread(allowed_request, current)
                        # Chromium routing does not re-intercept every redirect
                        # hop. Fail closed rather than automatically follow a hop
                        # that could bypass this policy or forward credentials.
                        response = await route.fetch(max_redirects=0)
                        if response.status in {301, 302, 303, 307, 308} and "location" in response.headers:
                            await response.dispose()
                            raise ValueError("automatic redirects are blocked; use verified final URL")
                        await route.fulfill(response=response)
                    except Exception as exc:
                        if not isinstance(exc, (ValueError, OSError)) and not _is_playwright_error(exc):
                            raise
                        blocked_requests.append(current)
                        await route.abort("blockedbyclient")

                await context.route("**/*", guarded_request)
                downloads: list[dict[str, object]] = []
                download_root = self.safe_screenshot(arguments.download_dir) if arguments.download_dir else None
                if download_root: download_root.mkdir(parents=True, exist_ok=True)
                async def save_download(download):
                    if not download_root: return
                    suggested = Path(download.suggested_filename).name
                    target = download_root / suggested
                    await download.save_as(target)
                    downloads.append({"filename":suggested,"path":str(target.relative_to(self.workspace))})
                page.on("download", save_download)
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
                text = (await page.locator("body").inner_text())[:200_000]
                lowered = text.lower()
                challenge = any(marker in lowered for marker in ("verify you are human", "captcha", "security challenge", "checking your browser"))
                result = {"url": page.url, "title": await page.title(), "text": text, "status": response.status if response else None, "screenshot": screenshot, "actions": events, "downloads": downloads, "blocked_requests": blocked_requests, "challenge": {"detected":challenge,"requires_human":challenge}}
                return result
            finally:
                if close is not None:
                    await close()

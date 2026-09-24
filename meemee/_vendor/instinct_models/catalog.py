"""Read-only catalog sources. ``AILibraryCatalog`` reads public listing pages of
https://www.theailibrary.co (AI tools directory and prompt library) so products can
browse tools and prompts. Read-only by design: there is no submit/list/launch call,
and it never posts to the site. It honours robots.txt, sends a clear user agent,
caches, and rate-limits. Listings are third-party content: show them as
suggestions with their source URL, never as endorsements, and never train on them.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Protocol

BASE = "https://www.theailibrary.co"
UA = "instinct-models-catalog/0.1 (read-only)"


@dataclass(frozen=True)
class CatalogItem:
    title: str
    url: str
    kind: str  # "tool" | "prompt" | "link"
    source: str


class CatalogSource(Protocol):
    name: str

    def browse(self, section: str = "", query: str | None = None, limit: int = 50) -> list[CatalogItem]:
        """Read-only listing of catalog items in `section`, optionally filtered by `query`."""


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def _get(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(3_000_000).decode("utf-8", "replace")


class AILibraryCatalog:
    name = "theailibrary.co"
    SECTIONS = {"": "/", "prompts": "/prompts", "ai": "/categories/ai"}

    def __init__(self, fetch: Callable[[str], str] = _get, min_interval_s: float = 2.0, ttl_s: float = 3600,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.fetch, self.min_interval_s, self.ttl_s, self.clock, self.sleep = fetch, min_interval_s, ttl_s, clock, sleep
        self._cache: dict[str, tuple[float, str]] = {}
        self._last = -1e9
        self._robots: urllib.robotparser.RobotFileParser | None = None

    def _allowed(self, url: str) -> bool:
        if self._robots is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.parse(self.fetch(BASE + "/robots.txt").splitlines())
            except Exception:  # noqa: BLE001 - unreadable robots: be conservative
                rp.parse(["User-agent: *", "Disallow: /"])
            self._robots = rp
        return self._robots.can_fetch(UA, url)

    def _page(self, url: str) -> str:
        hit = self._cache.get(url)
        if hit and self.clock() - hit[0] < self.ttl_s:
            return hit[1]
        if not self._allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        wait = self.min_interval_s - (self.clock() - self._last)
        if wait > 0:
            self.sleep(wait)
        html = self.fetch(url)
        self._last = self.clock()
        self._cache[url] = (self.clock(), html)
        return html

    def browse(self, section: str = "", query: str | None = None, limit: int = 50) -> list[CatalogItem]:
        if section not in self.SECTIONS:
            raise ValueError(f"section must be one of {sorted(self.SECTIONS)}")
        url = BASE + self.SECTIONS[section]
        p = _Links(); p.feed(self._page(url))
        items, seen = [], set()
        for href, text in p.links:
            if not href or not text or len(text) < 3:
                continue
            full = urllib.parse.urljoin(url, href)
            host = urllib.parse.urlsplit(full).hostname or ""
            if not host.endswith("theailibrary.co") or full in seen:
                continue
            path = urllib.parse.urlsplit(full).path
            if path in ("/", "/pricing", "/terms-of-service", "/privacy-policy", "/about-us") or path.startswith(("/login", "/signup", "/submit")):
                continue
            kind = "prompt" if "/prompt" in path else "tool" if re.search(r"/(tool|tools|ai-tools|product)s?/", path) else "link"
            if query and query.casefold() not in text.casefold():
                continue
            seen.add(full)
            items.append(CatalogItem(text[:200], full, kind, self.name))
            if len(items) >= limit:
                break
        return items

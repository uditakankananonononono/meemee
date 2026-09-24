"""Durable browser sessions with interactive human takeover.

The agent drives a live Chromium page. When a site shows a challenge (captcha,
"verify you are human", MFA prompt) or the agent asks for help, the session is
kept open, a takeover is created, and a person gets a one-time link to a live
view. The person sees streamed frames of the real page and their clicks, typing,
keys and scrolls are relayed into that same page. When they hand control back,
the agent continues on the same page with the same cookies.

All Playwright objects live on one dedicated event-loop thread, so the agent
loop, the API server and background reapers can share sessions safely even
when they run on different asyncio loops.

Only metadata is persisted: session state, takeover lifecycle and an event log.
Screenshots and typed text are never stored; typed input is logged by length.
Live pages cannot survive a process restart, so sessions that were open when
the process stopped are marked ``lost`` on the next start.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from playwright.async_api import Error as PlaywrightError
except ImportError:  # browser extra not installed; open() reports it
    class PlaywrightError(Exception):
        """Placeholder so except clauses stay valid without Playwright."""

PAGE_ERRORS = (PlaywrightError, asyncio.TimeoutError)

CHALLENGE_MARKERS = (
    "verify you are human",
    "verify you're human",
    "are you a robot",
    "i'm not a robot",
    "captcha",
    "security challenge",
    "checking your browser",
    "unusual traffic",
    "press and hold",
    "enter the code we sent",
    "two-step verification",
    "2-step verification",
)
CHALLENGE_FRAME_HINTS = ("recaptcha", "hcaptcha", "challenges.cloudflare.com", "arkoselabs", "funcaptcha")

SESSION_STATES = {"agent", "awaiting_human", "human", "closed", "lost"}
ALLOWED_KEYS = {
    "Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown", "ArrowLeft",
    "ArrowRight", "Home", "End", "PageUp", "PageDown", "Space",
}
MODIFIERS = {"Shift", "Control", "Alt", "Meta"}


class BrowserSessionError(ValueError):
    """Raised for invalid session operations; message is safe to show callers."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def detect_challenge(text: str, frame_urls: list[str] | None = None) -> dict[str, Any]:
    lowered = (text or "").lower()
    markers = [marker for marker in CHALLENGE_MARKERS if marker in lowered]
    frames = [url for url in (frame_urls or []) if any(hint in url.lower() for hint in CHALLENGE_FRAME_HINTS)]
    detected = bool(markers or frames)
    return {"detected": detected, "requires_human": detected, "markers": markers, "frames": frames[:5]}


def check_url(url: str, allowed_domains: list[str], allow_private_hosts: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserSessionError("browser URL must use http or https")
    hostname = parsed.hostname.lower()
    if allowed_domains and not any(hostname == d.lower() or hostname.endswith("." + d.lower()) for d in allowed_domains):
        raise BrowserSessionError("browser URL is outside the session domain policy")
    if not allow_private_hosts:
        try:
            addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise BrowserSessionError(f"cannot resolve {hostname}") from exc
        for address in addresses:
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise BrowserSessionError("browser URL resolves to a private or reserved address")
    return url


class BrowserSessionStore:
    """SQLite record of sessions, takeovers and events."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL,
                    profile TEXT, allowed_domains TEXT NOT NULL, url TEXT, title TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    closed_at TEXT, close_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS browser_takeovers (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES browser_sessions(id),
                    token_digest BLOB NOT NULL, reason TEXT NOT NULL, requested_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    claimed_at TEXT, finished_at TEXT, outcome TEXT, note TEXT
                );
                CREATE TABLE IF NOT EXISTS browser_session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    ts TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS browser_events_session ON browser_session_events(session_id, id);
                CREATE INDEX IF NOT EXISTS browser_takeovers_session ON browser_takeovers(session_id);
            """)

    def mark_open_sessions_lost(self) -> int:
        now = _iso(_now())
        with self.lock, self.db:
            rows = [r["id"] for r in self.db.execute("SELECT id FROM browser_sessions WHERE state NOT IN ('closed','lost')")]
            self.db.execute("UPDATE browser_sessions SET state='lost', closed_at=?, close_reason='process_restart', updated_at=? WHERE state NOT IN ('closed','lost')", (now, now))
            self.db.execute("UPDATE browser_takeovers SET finished_at=?, outcome='lost' WHERE finished_at IS NULL", (now,))
        return len(rows)

    def create_session(self, session_id: str, owner_id: str, profile: str | None, allowed_domains: list[str]) -> None:
        now = _iso(_now())
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO browser_sessions(id, owner_id, state, profile, allowed_domains, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (session_id, owner_id, "agent", profile, json.dumps(allowed_domains), now, now),
            )

    def update_session(self, session_id: str, **values: Any) -> None:
        allowed = {"state", "url", "title", "closed_at", "close_reason"}
        if not values or not set(values) <= allowed:
            raise ValueError("invalid session update")
        values["updated_at"] = _iso(_now())
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.lock, self.db:
            self.db.execute(f"UPDATE browser_sessions SET {assignments} WHERE id=?", (*values.values(), session_id))

    def event(self, session_id: str, actor: str, kind: str, detail: dict[str, Any] | None = None) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO browser_session_events(session_id, ts, actor, kind, detail) VALUES (?,?,?,?,?)",
                (session_id, _iso(_now()), actor, kind, json.dumps(detail or {}, sort_keys=True)),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM browser_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["allowed_domains"] = json.loads(record["allowed_domains"])
        return record

    def list_sessions(self, owner_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.lock:
            if owner_id is None:
                rows = self.db.execute("SELECT * FROM browser_sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = self.db.execute("SELECT * FROM browser_sessions WHERE owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_id, limit)).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            record["allowed_domains"] = json.loads(record["allowed_domains"])
            out.append(record)
        return out

    def events(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, ts, actor, kind, detail FROM browser_session_events WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in reversed(rows)]

    def create_takeover(self, takeover_id: str, session_id: str, token: str, reason: str, requested_by: str, expires_at: datetime) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO browser_takeovers(id, session_id, token_digest, reason, requested_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                (takeover_id, session_id, _digest(token), reason, requested_by, _iso(_now()), _iso(expires_at)),
            )

    def get_takeover(self, takeover_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM browser_takeovers WHERE id=?", (takeover_id,)).fetchone()
        return dict(row) if row else None

    def session_takeovers(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, reason, requested_by, created_at, expires_at, claimed_at, finished_at, outcome, note FROM browser_takeovers WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def claim_takeover(self, takeover_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE browser_takeovers SET claimed_at=COALESCE(claimed_at, ?) WHERE id=?", (_iso(_now()), takeover_id))

    def finish_takeover(self, takeover_id: str, outcome: str, note: str | None = None) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE browser_takeovers SET finished_at=?, outcome=?, note=? WHERE id=? AND finished_at IS NULL",
                (_iso(_now()), outcome, note, takeover_id),
            )
        return cursor.rowcount == 1


@dataclass
class _Live:
    id: str
    owner_id: str
    allowed_domains: list[str]
    context: Any
    page: Any
    browser: Any | None
    lock: asyncio.Lock
    last_activity: float
    takeover_id: str | None = None
    takeover_expires: float | None = None
    released: threading.Event = field(default_factory=threading.Event)
    downloads: list[str] = field(default_factory=list)
    notify_user_id: str | None = None


class BrowserSessionManager:
    """Owns live Chromium sessions on a dedicated loop thread."""

    def __init__(
        self,
        store: BrowserSessionStore,
        *,
        headless: bool = True,
        profiles_dir: Path | None = None,
        public_url: str = "http://localhost:8787",
        allow_private_hosts: bool = False,
        max_sessions: int = 4,
        idle_timeout_seconds: float = 900,
        takeover_ttl_seconds: float = 900,
        viewport: tuple[int, int] = (1280, 800),
        notices=None,
    ):
        self.store = store
        self.headless = headless
        self.profiles_dir = profiles_dir
        self.public_url = public_url.rstrip("/")
        self.allow_private_hosts = allow_private_hosts
        self.max_sessions = max_sessions
        self.idle_timeout = idle_timeout_seconds
        self.takeover_ttl = takeover_ttl_seconds
        self.viewport = viewport
        self.notices = notices
        self.notice_delivery = None  # async callable set by the host process
        self._live: dict[str, _Live] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._playwright = None
        self._browser = None
        self._start_lock = threading.Lock()
        self.store.mark_open_sessions_lost()

    # ----- loop plumbing -------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def runner() -> None:
                    asyncio.set_event_loop(loop)
                    ready.set()
                    loop.run_forever()

                self._thread = threading.Thread(target=runner, name="meemee-browser", daemon=True)
                self._thread.start()
                ready.wait()
                self._loop = loop
        return self._loop

    def _submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    async def _call(self, coro):
        return await asyncio.wrap_future(self._submit(coro))

    def call_sync(self, coro, timeout: float | None = 60):
        return self._submit(coro).result(timeout)

    # ----- helpers on the browser loop ----------------------------------
    async def _ensure_browser(self):
        if self._playwright is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserSessionError("browser extra is not installed; run pip install 'meemee-agent[browser]' and playwright install chromium") from exc
            self._playwright = await async_playwright().start()
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        return self._browser

    def _get(self, session_id: str) -> _Live:
        live = self._live.get(session_id)
        if live is None:
            record = self.store.get_session(session_id)
            if record is None:
                raise BrowserSessionError("unknown browser session")
            raise BrowserSessionError(f"browser session is {record['state']}")
        return live

    async def _state(self, live: _Live, include_text: bool = True, text_limit: int = 20_000) -> dict[str, Any]:
        page = live.page
        try:
            text = await page.locator("body").inner_text(timeout=5_000)
        except PAGE_ERRORS:  # page may be mid-navigation
            text = ""
        frames = [frame.url for frame in page.frames]
        challenge = detect_challenge(text, frames)
        title = await page.title()
        self.store.update_session(live.id, url=page.url, title=title[:500])
        record = self.store.get_session(live.id) or {}
        result: dict[str, Any] = {
            "session_id": live.id,
            "state": record.get("state"),
            "url": page.url,
            "title": title,
            "challenge": challenge,
            "downloads": list(live.downloads),
        }
        if include_text:
            result["text"] = text[:text_limit]
        return result

    # ----- agent operations ---------------------------------------------
    async def open(self, url: str, owner_id: str = "agent", allowed_domains: list[str] | None = None,
                   profile: str | None = None, auto_takeover: bool = True, notify_user_id: str | None = None) -> dict[str, Any]:
        state = await self._call(self._open(url, owner_id, list(allowed_domains or []), profile, auto_takeover, notify_user_id))
        await self.deliver_notices()
        return state

    async def deliver_notices(self) -> list[dict[str, Any]]:
        """Deliver queued takeover notices on the caller's loop (channel clients live there)."""
        if self.notices is None or self.notice_delivery is None:
            return []
        return await self.notice_delivery()

    async def _open(self, url: str, owner_id: str, allowed_domains: list[str], profile: str | None, auto_takeover: bool,
                    notify_user_id: str | None = None) -> dict[str, Any]:
        check_url(url, allowed_domains, self.allow_private_hosts)
        await self._reap()
        if len(self._live) >= self.max_sessions:
            raise BrowserSessionError(f"browser session limit reached ({self.max_sessions}); close one first")
        browser = None
        if profile:
            if self.profiles_dir is None:
                raise BrowserSessionError("persistent profiles are not configured")
            if not all(ch.isalnum() or ch in "_.-" for ch in profile) or len(profile) > 64:
                raise BrowserSessionError("invalid profile name")
            if any(l.context and getattr(l, "profile", None) == profile for l in self._live.values()):
                raise BrowserSessionError("profile is already in use by an open session")
            await self._ensure_browser()
            context = await self._playwright.chromium.launch_persistent_context(
                str(self.profiles_dir / profile), headless=self.headless,
                viewport={"width": self.viewport[0], "height": self.viewport[1]},
            )
        else:
            browser = await self._ensure_browser()
            context = await browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]})
        page = context.pages[0] if context.pages else await context.new_page()
        session_id = "bs_" + uuid.uuid4().hex
        live = _Live(session_id, owner_id, allowed_domains, context, page, browser, asyncio.Lock(), time.monotonic())
        live.profile = profile  # type: ignore[attr-defined]
        live.notify_user_id = notify_user_id

        async def guard(route):
            try:
                check_url(route.request.url, allowed_domains, self.allow_private_hosts)
            except BrowserSessionError:
                if route.request.is_navigation_request():
                    self.store.event(session_id, "system", "navigation_blocked", {"url": route.request.url[:500]})
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        if allowed_domains:
            await context.route("**/*", guard)
        self.store.create_session(session_id, owner_id, profile, allowed_domains)
        self._live[session_id] = live
        self.store.event(session_id, owner_id, "opened", {"url": url[:500], "profile": profile})
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PAGE_ERRORS as exc:
            self.store.event(session_id, "system", "navigation_error", {"error": str(exc)[:500]})
        state = await self._state(live)
        if auto_takeover and state["challenge"]["detected"]:
            state["takeover"] = await self._request_takeover(live, "challenge detected: " + ", ".join(state["challenge"]["markers"] or state["challenge"]["frames"])[:200], "system")
            state["state"] = "awaiting_human"
        return state

    async def act(self, session_id: str, actions: list[dict[str, Any]], auto_takeover: bool = True) -> dict[str, Any]:
        state = await self._call(self._act(session_id, actions, auto_takeover))
        await self.deliver_notices()
        return state

    async def _act(self, session_id: str, actions: list[dict[str, Any]], auto_takeover: bool) -> dict[str, Any]:
        live = self._get(session_id)
        record = self.store.get_session(session_id) or {}
        if record.get("state") != "agent":
            raise BrowserSessionError(f"agent cannot act while session is {record.get('state')}; wait for the human to hand back control")
        events = []
        async with live.lock:
            live.last_activity = time.monotonic()
            page = live.page
            for index, action in enumerate(actions):
                kind = action.get("kind")
                selector = action.get("selector")
                value = action.get("value")
                if kind == "goto":
                    check_url(value or "", live.allowed_domains, self.allow_private_hosts)
                    await page.goto(value, wait_until="domcontentloaded", timeout=30_000)
                elif kind == "wait":
                    await page.wait_for_timeout(min(int(action.get("milliseconds") or 0), 10_000))
                elif kind in {"click", "fill", "press", "select"}:
                    if not selector:
                        raise BrowserSessionError(f"action {index} requires selector")
                    locator = page.locator(selector).first
                    if kind == "click":
                        await locator.click(timeout=10_000)
                    elif kind == "fill":
                        await locator.fill(value or "", timeout=10_000)
                    elif kind == "press":
                        await locator.press(value or "Enter", timeout=10_000)
                    else:
                        await locator.select_option(value or "", timeout=10_000)
                else:
                    raise BrowserSessionError(f"unsupported action kind: {kind}")
                events.append({"index": index, "kind": kind, "url": page.url})
            self.store.event(session_id, live.owner_id, "agent_actions", {"count": len(events), "kinds": [e["kind"] for e in events]})
            state = await self._state(live)
        state["actions"] = events
        if auto_takeover and state["challenge"]["detected"]:
            state["takeover"] = await self._request_takeover(live, "challenge detected", "system")
            state["state"] = "awaiting_human"
        return state

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        async def run():
            live = self._get(session_id)
            async with live.lock:
                return await self._state(live)
        return await self._call(run())

    async def screenshot(self, session_id: str, quality: int = 60) -> bytes:
        async def run():
            live = self._get(session_id)
            return await live.page.screenshot(type="jpeg", quality=max(20, min(quality, 90)))
        return await self._call(run())

    async def request_takeover(self, session_id: str, reason: str, requested_by: str = "agent") -> dict[str, Any]:
        async def run():
            return await self._request_takeover(self._get(session_id), reason, requested_by)
        takeover = await self._call(run())
        await self.deliver_notices()
        return takeover

    async def _request_takeover(self, live: _Live, reason: str, requested_by: str) -> dict[str, Any]:
        record = self.store.get_session(live.id) or {}
        if record.get("state") in {"awaiting_human", "human"} and live.takeover_id:
            self.store.finish_takeover(live.takeover_id, "superseded")
        token = secrets.token_urlsafe(32)
        takeover_id = "bt_" + uuid.uuid4().hex
        expires = _now() + timedelta(seconds=self.takeover_ttl)
        self.store.create_takeover(takeover_id, live.id, token, reason[:500], requested_by, expires)
        live.takeover_id = takeover_id
        live.takeover_expires = time.monotonic() + self.takeover_ttl
        live.released.clear()
        self.store.update_session(live.id, state="awaiting_human")
        self.store.event(live.id, requested_by, "takeover_requested", {"takeover_id": takeover_id, "reason": reason[:500]})
        takeover = {
            "takeover_id": takeover_id,
            "token": token,
            "expires_at": _iso(expires),
            "reason": reason[:500],
            "url": f"{self.public_url}/browser/takeover#{takeover_id}.{token}",
        }
        if self.notices is not None and live.notify_user_id:
            self.notices.enqueue(takeover, live.id, live.notify_user_id)
            self.store.event(live.id, "system", "notice_queued", {"takeover_id": takeover_id, "user_id": live.notify_user_id})
            takeover["notice_queued_for"] = live.notify_user_id
        return takeover

    async def wait_for_human(self, session_id: str, timeout_seconds: float = 600) -> dict[str, Any]:
        """Block (without holding the loop) until the human hands back control or the takeover ends."""
        live = self._live.get(session_id)
        if live is None:
            self._get(session_id)
        deadline = time.monotonic() + max(0.0, min(timeout_seconds, 3600))
        while time.monotonic() < deadline:
            if live.released.is_set():
                break
            await self._call(self._reap())
            if session_id not in self._live:
                break
            record = self.store.get_session(session_id) or {}
            if record.get("state") == "agent":
                break
            await asyncio.sleep(0.25)
        record = self.store.get_session(session_id) or {}
        takeover = self.store.get_takeover(live.takeover_id) if live.takeover_id else None
        outcome = (takeover or {}).get("outcome") or ("pending" if record.get("state") in {"awaiting_human", "human"} else None)
        result = {"session_id": session_id, "state": record.get("state"), "takeover_outcome": outcome,
                  "note": (takeover or {}).get("note")}
        if session_id in self._live and record.get("state") == "agent":
            result.update(await self.snapshot(session_id))
            result["takeover_outcome"] = outcome
        return result

    async def close(self, session_id: str, actor: str = "agent", reason: str = "closed") -> dict[str, Any]:
        return await self._call(self._close(session_id, actor, reason))

    async def _close(self, session_id: str, actor: str, reason: str) -> dict[str, Any]:
        live = self._live.pop(session_id, None)
        if live is None:
            record = self.store.get_session(session_id)
            if record is None:
                raise BrowserSessionError("unknown browser session")
            return {"session_id": session_id, "state": record["state"]}
        if live.takeover_id:
            self.store.finish_takeover(live.takeover_id, "session_closed")
        live.released.set()
        try:
            await live.context.close()
        except PAGE_ERRORS as exc:
            self.store.event(session_id, "system", "close_error", {"error": str(exc)[:500]})
        self.store.update_session(session_id, state="closed", closed_at=_iso(_now()), close_reason=reason[:200])
        self.store.event(session_id, actor, "closed", {"reason": reason[:200]})
        return {"session_id": session_id, "state": "closed"}

    async def _reap(self) -> None:
        now = time.monotonic()
        for session_id, live in list(self._live.items()):
            record = self.store.get_session(session_id) or {}
            if live.takeover_id and live.takeover_expires and now > live.takeover_expires and record.get("state") in {"awaiting_human", "human"}:
                self.store.finish_takeover(live.takeover_id, "expired")
                self.store.update_session(session_id, state="agent")
                self.store.event(session_id, "system", "takeover_expired", {"takeover_id": live.takeover_id})
                live.takeover_expires = None
                live.released.set()
            elif record.get("state") == "agent" and now - live.last_activity > self.idle_timeout:
                await self._close(session_id, "system", "idle_timeout")

    async def reap(self) -> None:
        await self._call(self._reap())

    # ----- human side ----------------------------------------------------
    def authenticate_takeover(self, takeover_id: str, token: str) -> dict[str, Any]:
        takeover = self.store.get_takeover(takeover_id)
        if takeover is None or not hmac.compare_digest(bytes(takeover["token_digest"]), _digest(token or "")):
            raise BrowserSessionError("invalid takeover link")
        if takeover["finished_at"] is not None:
            raise BrowserSessionError(f"takeover already ended ({takeover['outcome']})")
        if datetime.fromisoformat(takeover["expires_at"]) < _now():
            raise BrowserSessionError("takeover link expired")
        live = self._live.get(takeover["session_id"])
        if live is None or live.takeover_id != takeover_id:
            raise BrowserSessionError("browser session is no longer live")
        return takeover

    async def claim(self, takeover_id: str, token: str) -> dict[str, Any]:
        takeover = self.authenticate_takeover(takeover_id, token)
        session_id = takeover["session_id"]
        self.store.claim_takeover(takeover_id)
        self.store.update_session(session_id, state="human")
        self.store.event(session_id, "human", "takeover_claimed", {"takeover_id": takeover_id})
        live = self._live[session_id]
        live.last_activity = time.monotonic()
        return {"session_id": session_id, "reason": takeover["reason"], "viewport": {"width": self.viewport[0], "height": self.viewport[1]}}

    async def human_input(self, takeover_id: str, token: str, event: dict[str, Any]) -> dict[str, Any]:
        takeover = self.authenticate_takeover(takeover_id, token)
        session_id = takeover["session_id"]
        record = self.store.get_session(session_id) or {}
        if record.get("state") != "human":
            raise BrowserSessionError("claim the takeover before sending input")
        return await self._call(self._human_input(self._live[session_id], event))

    async def _human_input(self, live: _Live, event: dict[str, Any]) -> dict[str, Any]:
        kind = event.get("type")
        page = live.page
        width, height = self.viewport
        async with live.lock:
            live.last_activity = time.monotonic()
            if live.takeover_expires:
                live.takeover_expires = max(live.takeover_expires, time.monotonic() + 120)
            if kind == "click":
                x, y = float(event.get("x", -1)), float(event.get("y", -1))
                if not (0 <= x <= width and 0 <= y <= height):
                    raise BrowserSessionError("click outside viewport")
                button = event.get("button", "left")
                if button not in {"left", "right", "middle"}:
                    raise BrowserSessionError("invalid mouse button")
                await page.mouse.click(x, y, button=button, click_count=1 if event.get("double") is not True else 2)
                detail = {"x": round(x), "y": round(y)}
            elif kind == "drag":
                start, end = event.get("from"), event.get("to")
                if not (isinstance(start, list) and isinstance(end, list) and len(start) == 2 and len(end) == 2):
                    raise BrowserSessionError("drag needs from=[x,y] and to=[x,y]")
                (x1, y1), (x2, y2) = (float(v) for v in start), (float(v) for v in end)
                if not all(0 <= x <= width for x in (x1, x2)) or not all(0 <= y <= height for y in (y1, y2)):
                    raise BrowserSessionError("drag outside viewport")
                steps = max(2, min(int(event.get("steps", 20)), 100))
                await page.mouse.move(x1, y1)
                await page.mouse.down()
                await page.mouse.move(x2, y2, steps=steps)
                await page.mouse.up()
                detail = {"from": [round(x1), round(y1)], "to": [round(x2), round(y2)]}
            elif kind == "type":
                text = str(event.get("text", ""))
                if not 0 < len(text) <= 2000:
                    raise BrowserSessionError("text must be 1-2000 characters")
                await page.keyboard.type(text)
                detail = {"chars": len(text)}
            elif kind == "key":
                key = str(event.get("key", ""))
                parts = key.split("+")
                base = parts[-1]
                if not all(p in MODIFIERS for p in parts[:-1]) or not (base in ALLOWED_KEYS or (len(base) == 1 and base.isprintable())):
                    raise BrowserSessionError("key not allowed")
                await page.keyboard.press(key)
                detail = {"key": key if len(base) > 1 else "<char>"}
            elif kind == "scroll":
                dx, dy = float(event.get("dx", 0)), float(event.get("dy", 0))
                await page.mouse.wheel(max(-5000, min(dx, 5000)), max(-5000, min(dy, 5000)))
                detail = {"dy": round(dy)}
            elif kind == "goto":
                url = str(event.get("url", ""))
                check_url(url, live.allowed_domains, self.allow_private_hosts)
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                detail = {"url": url[:500]}
            else:
                raise BrowserSessionError("unsupported input event")
        self.store.event(live.id, "human", f"human_{kind}", detail)
        return {"ok": True, "url": page.url}

    async def release(self, takeover_id: str, token: str, note: str | None = None, outcome: str = "completed") -> dict[str, Any]:
        takeover = self.authenticate_takeover(takeover_id, token)
        if outcome not in {"completed", "declined"}:
            raise BrowserSessionError("outcome must be completed or declined")
        session_id = takeover["session_id"]
        live = self._live[session_id]
        self.store.finish_takeover(takeover_id, outcome, (note or "")[:1000] or None)
        self.store.update_session(session_id, state="agent")
        self.store.event(session_id, "human", "takeover_released", {"takeover_id": takeover_id, "outcome": outcome})
        live.takeover_expires = None
        live.last_activity = time.monotonic()
        live.released.set()
        return {"session_id": session_id, "state": "agent", "outcome": outcome}

    async def frame(self, takeover_id: str, token: str, quality: int = 60) -> dict[str, Any]:
        takeover = self.authenticate_takeover(takeover_id, token)
        session_id = takeover["session_id"]
        image = await self.screenshot(session_id, quality)
        live = self._live[session_id]
        return {"type": "frame", "jpeg": base64.b64encode(image).decode(), "url": live.page.url}

    # ----- shutdown ------------------------------------------------------
    def shutdown(self) -> None:
        if self._loop is None:
            return

        async def stop():
            for session_id in list(self._live):
                await self._close(session_id, "system", "shutdown")
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._browser = self._playwright = None

        try:
            self._submit(stop()).result(30)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(5)
            self._loop = None

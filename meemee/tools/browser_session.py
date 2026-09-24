"""Agent tools over live browser sessions with human takeover."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..browser_sessions import BrowserSessionManager
from ..types import Risk
from .base import Tool


class SessionAction(BaseModel):
    kind: Literal["goto", "click", "fill", "press", "select", "wait"]
    selector: str | None = Field(default=None, max_length=1000)
    value: str | None = Field(default=None, max_length=20_000)
    milliseconds: int | None = Field(default=None, ge=0, le=10_000)


class OpenArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)
    profile: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    auto_takeover: bool = True
    notify_user_id: str | None = Field(default=None, min_length=1, max_length=120,
                                       description="Companion user to notify with the takeover link on their configured channel")


class ActArgs(BaseModel):
    session_id: str = Field(pattern=r"^bs_[0-9a-f]{32}$")
    actions: list[SessionAction] = Field(min_length=1, max_length=30)
    auto_takeover: bool = True


class SessionArgs(BaseModel):
    session_id: str = Field(pattern=r"^bs_[0-9a-f]{32}$")


class HumanArgs(BaseModel):
    session_id: str = Field(pattern=r"^bs_[0-9a-f]{32}$")
    reason: str = Field(min_length=3, max_length=500)


class WaitArgs(BaseModel):
    session_id: str = Field(pattern=r"^bs_[0-9a-f]{32}$")
    timeout_seconds: float = Field(default=600, ge=1, le=3600)


def _public(state: dict) -> dict:
    """The takeover token is a capability for the human; keep it in the link only."""
    takeover = state.get("takeover")
    if takeover:
        state = {**state, "takeover": {k: v for k, v in takeover.items() if k != "token"}}
    return state


class _SessionTool(Tool):
    def __init__(self, manager: BrowserSessionManager):
        self.manager = manager


class BrowserSessionOpen(_SessionTool):
    name = "browser.session_open"
    description = ("Open a live browser session that stays open across steps. If the page shows a captcha or "
                   "verification challenge, a human takeover link is created and returned; send it to the person "
                   "and call browser.session_wait_human.")
    risk = Risk.WRITE
    arguments_model = OpenArgs

    async def run(self, arguments: OpenArgs):
        return _public(await self.manager.open(arguments.url, "agent", arguments.allowed_domains, arguments.profile, arguments.auto_takeover, arguments.notify_user_id))


class BrowserSessionAct(_SessionTool):
    name = "browser.session_act"
    description = "Run goto/click/fill/press/select/wait actions in an open browser session and return the new page state."
    risk = Risk.WRITE
    arguments_model = ActArgs

    async def run(self, arguments: ActArgs):
        actions = [a.model_dump() for a in arguments.actions]
        return _public(await self.manager.act(arguments.session_id, actions, arguments.auto_takeover))


class BrowserSessionRequestHuman(_SessionTool):
    name = "browser.session_request_human"
    description = "Ask a person to take control of the live page (login, MFA, captcha, judgment call). Returns a one-time live-control link."
    risk = Risk.WRITE
    arguments_model = HumanArgs

    async def run(self, arguments: HumanArgs):
        takeover = await self.manager.request_takeover(arguments.session_id, arguments.reason, "agent")
        return {k: v for k, v in takeover.items() if k != "token"}


class BrowserSessionWaitHuman(_SessionTool):
    name = "browser.session_wait_human"
    description = "Wait until the person hands control back (or the takeover expires), then return the current page state."
    risk = Risk.READ
    arguments_model = WaitArgs

    async def run(self, arguments: WaitArgs):
        return await self.manager.wait_for_human(arguments.session_id, arguments.timeout_seconds)


class BrowserSessionSnapshot(_SessionTool):
    name = "browser.session_snapshot"
    description = "Read the current URL, title, text and challenge status of an open browser session."
    risk = Risk.READ
    arguments_model = SessionArgs

    async def run(self, arguments: SessionArgs):
        return await self.manager.snapshot(arguments.session_id)


class BrowserSessionClose(_SessionTool):
    name = "browser.session_close"
    description = "Close an open browser session."
    risk = Risk.WRITE
    arguments_model = SessionArgs

    async def run(self, arguments: SessionArgs):
        return await self.manager.close(arguments.session_id, "agent")


def session_tools(manager: BrowserSessionManager) -> list[Tool]:
    return [cls(manager) for cls in (BrowserSessionOpen, BrowserSessionAct, BrowserSessionRequestHuman,
                                     BrowserSessionWaitHuman, BrowserSessionSnapshot, BrowserSessionClose)]

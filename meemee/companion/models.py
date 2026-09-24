from __future__ import annotations

import re
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

USER_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PersonaConfig(BaseModel):
    """How the companion speaks for and to one user."""

    display_name: str = Field(default="Meemee", min_length=1, max_length=60)
    tone: str = Field(default="warm, direct and honest", min_length=1, max_length=200)
    style_rules: list[str] = Field(default_factory=list, max_length=20)
    language: str = Field(default="en", min_length=2, max_length=20)
    use_emoji: bool = False
    custom_instructions: str = Field(default="", max_length=2000)

    @field_validator("style_rules")
    @classmethod
    def bounded_rules(cls, value: list[str]) -> list[str]:
        cleaned = [rule.strip() for rule in value if rule.strip()]
        for rule in cleaned:
            if len(rule) > 300:
                raise ValueError("each style rule must be at most 300 characters")
        return cleaned


class QuietHours(BaseModel):
    """Local wall-clock window during which proactive check-ins stay silent."""

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def hhmm(cls, value: str) -> str:
        if not HHMM.match(value):
            raise ValueError("quiet hours use HH:MM 24-hour local time")
        return value


class CheckInPreferences(BaseModel):
    enabled: bool = False
    cadence_minutes: int = Field(default=360, ge=15, le=10080)
    quiet_hours: QuietHours | None = None
    channel: str = Field(default="local", min_length=1, max_length=40)
    address: str | None = Field(default=None, max_length=500)


class UserProfile(BaseModel):
    user_id: str
    display_name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=60)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    checkins: CheckInPreferences = Field(default_factory=CheckInPreferences)

    @field_validator("user_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not USER_ID.match(value):
            raise ValueError("user_id must be 1-80 characters of letters, digits, dot, dash or underscore")
        return value

    @field_validator("timezone")
    @classmethod
    def real_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class FactInput(BaseModel):
    category: str = Field(default="general", min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ChatRequest(BaseModel):
    user_id: str
    text: str = Field(min_length=1, max_length=8000)
    channel: str = Field(default="local", min_length=1, max_length=40)
    conversation_id: str | None = Field(default=None, max_length=80)


class ChatReply(BaseModel):
    conversation_id: str
    reply: str
    facts_learned: int
    persona: str
    model_trace: dict[str, Any] | None = None


class CheckInPlanRequest(BaseModel):
    at: str | None = None


class DeliveryRecord(BaseModel):
    checkin_id: str
    channel: str
    delivered: bool
    detail: str


MessageRole = Literal["user", "assistant", "system"]

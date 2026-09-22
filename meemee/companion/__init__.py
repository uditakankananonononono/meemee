"""Companion layer: persistent per-user memory, persona, proactive check-ins and channels."""

from .channels import (
    ChannelError,
    ChannelNotConfiguredError,
    DeliveryResult,
    LocalChannel,
    ProviderChannel,
    WebhookChannel,
    build_channels,
)
from .checkins import CheckInScheduler, in_quiet_hours, next_due
from .engine import CompanionEngine
from .models import (
    ChatReply,
    ChatRequest,
    CheckInPreferences,
    FactInput,
    PersonaConfig,
    QuietHours,
    UserProfile,
)
from .runtime import Companion, build_companion
from .store import CompanionStore

__all__ = [
    "ChannelError",
    "ChannelNotConfiguredError",
    "ChatReply",
    "ChatRequest",
    "CheckInPreferences",
    "CheckInScheduler",
    "Companion",
    "CompanionEngine",
    "CompanionStore",
    "DeliveryResult",
    "FactInput",
    "LocalChannel",
    "PersonaConfig",
    "ProviderChannel",
    "QuietHours",
    "UserProfile",
    "WebhookChannel",
    "build_channels",
    "build_companion",
    "in_quiet_hours",
    "next_due",
]

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..webhooks import validate_webhook_url


class ChannelError(RuntimeError):
    """A configured channel failed to deliver."""


class ChannelNotConfiguredError(ChannelError):
    """The channel's provider credentials or endpoint are not configured."""


@dataclass
class DeliveryResult:
    channel: str
    address: str
    delivered: bool
    detail: str


class ChannelAdapter(Protocol):
    name: str

    async def send(self, address: str, text: str) -> DeliveryResult: ...


class LocalChannel:
    """Default channel: persists outbound messages into the user's local conversation."""

    name = "local"

    def __init__(self, store):
        self.store = store

    async def send(self, address: str, text: str) -> DeliveryResult:
        conversation = self.store.get_conversation(address)
        if conversation is None:
            raise ChannelError(f"unknown local conversation: {address}")
        self.store.add_message(address, "assistant", text)
        return DeliveryResult(self.name, address, True, "stored in local conversation")


class WebhookChannel:
    """Signed HTTPS delivery of companion messages to a caller-owned endpoint."""

    name = "webhook"

    def __init__(
        self,
        secret: str | None = None,
        timeout: float = 15.0,
        max_payload_bytes: int = 64_000,
        client: httpx.AsyncClient | None = None,
    ):
        self.secret = secret
        self.max_payload_bytes = max_payload_bytes
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.owns_client = client is None

    async def send(self, address: str, text: str) -> DeliveryResult:
        url = validate_webhook_url(address)
        body = json.dumps({"kind": "companion.message", "text": text}, separators=(",", ":"))
        if len(body.encode()) > self.max_payload_bytes:
            raise ChannelError("companion message exceeds the webhook payload ceiling")
        headers = {"Content-Type": "application/json"}
        if self.secret:
            timestamp = str(int(time.time()))
            signature = hmac.new(
                self.secret.encode(), f"{timestamp}.".encode() + body.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Meemee-Timestamp"] = timestamp
            headers["X-Meemee-Signature"] = f"sha256={signature}"
        try:
            response = await self.client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ChannelError(f"webhook delivery failed: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelError(f"webhook endpoint returned HTTP {response.status_code}")
        return DeliveryResult(self.name, url, True, f"HTTP {response.status_code}")

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()


class ProviderChannel:
    """Generic chat-provider adapter used by the WhatsApp and iMessage channels.

    Delivery is config-gated: without a provider endpoint and token the channel
    fails closed with an explicit configuration error instead of pretending to send.
    """

    def __init__(
        self,
        name: str,
        provider_url: str | None,
        provider_token: str | None,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self.provider_url = provider_url
        self.provider_token = provider_token
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.owns_client = client is None

    def configured(self) -> bool:
        return bool(self.provider_url and self.provider_token)

    async def send(self, address: str, text: str) -> DeliveryResult:
        if not self.configured():
            prefix = f"MEEMEE_{self.name.upper()}_PROVIDER"
            raise ChannelNotConfiguredError(
                f"{self.name} delivery is not configured: set {prefix}_URL and {prefix}_TOKEN "
                "for a real provider account"
            )
        url = validate_webhook_url(self.provider_url or "")
        try:
            response = await self.client.post(
                f"{url.rstrip('/')}/messages",
                headers={"Authorization": f"Bearer {self.provider_token}"},
                json={"to": address, "text": text},
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"{self.name} provider delivery failed: {exc}") from exc
        if response.status_code >= 400:
            raise ChannelError(f"{self.name} provider returned HTTP {response.status_code}")
        return DeliveryResult(self.name, address, True, f"HTTP {response.status_code}")

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()


def build_channels(settings, store, client: httpx.AsyncClient | None = None) -> dict[str, ChannelAdapter]:
    """Compose the channel registry from runtime settings."""
    return {
        "local": LocalChannel(store),
        "webhook": WebhookChannel(
            secret=settings.companion_webhook_secret or None,
            max_payload_bytes=settings.webhook_max_payload_bytes,
            client=client,
        ),
        "whatsapp": ProviderChannel(
            "whatsapp", settings.whatsapp_provider_url, settings.whatsapp_provider_token, client=client
        ),
        "imessage": ProviderChannel(
            "imessage", settings.imessage_provider_url, settings.imessage_provider_token, client=client
        ),
    }

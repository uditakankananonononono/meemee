from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from .channels import ChannelError
from .checkins import CheckInScheduler
from .engine import CompanionEngine
from .store import CompanionStore

log = logging.getLogger("meemee.companion.worker")


async def deliver_due_once(
    store: CompanionStore,
    engine: CompanionEngine,
    channels: dict,
) -> dict:
    """Claim and deliver at most one due check-in. Returns a delivery summary."""
    checkin = store.claim_checkin()
    if checkin is None:
        return {"claimed": False}
    channel_name = checkin["channel"]
    adapter = channels.get(channel_name)
    try:
        if adapter is None:
            raise ChannelError(f"unknown companion channel: {channel_name}")
        message = await engine.checkin_message(checkin["user_id"])
        address = checkin["address"]
        if not address:
            if channel_name != "local":
                raise ChannelError(
                    f"check-in {checkin['id']} has no delivery address for channel {channel_name}"
                )
            conversation = store.latest_conversation(checkin["user_id"], "local")
            if conversation is None:
                conversation = store.start_conversation(checkin["user_id"], "local")
            address = conversation["id"]
        result = await adapter.send(address, message)
        store.finish_checkin(checkin["id"], message)
        return {
            "claimed": True,
            "checkin_id": checkin["id"],
            "user_id": checkin["user_id"],
            "delivered": result.delivered,
            "detail": result.detail,
        }
    except (ChannelError, ValueError, RuntimeError) as exc:
        state = store.fail_checkin(checkin["id"], str(exc))
        log.warning("check-in %s delivery failed (%s)", checkin["id"], exc)
        return {
            "claimed": True,
            "checkin_id": checkin["id"],
            "user_id": checkin["user_id"],
            "delivered": False,
            "status": state,
            "detail": str(exc),
        }


async def checkin_forever(settings: Settings | None = None) -> None:
    """Durable proactive check-in loop: plan slots, then deliver what is due."""
    settings = settings or Settings()
    from .runtime import build_companion

    companion = build_companion(settings)
    scheduler = CheckInScheduler(companion.store)
    while True:
        scheduler.plan_all()
        summary = await deliver_due_once(companion.store, companion.engine, companion.channels)
        if not summary["claimed"]:
            await asyncio.sleep(settings.companion_checkin_poll_seconds)

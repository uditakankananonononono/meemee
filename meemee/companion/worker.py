from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..config import Settings
from .channels import ChannelError, DeliveryOutcomeUnknown, LocalChannel, validate_delivery_address
from .checkins import CheckInScheduler, in_quiet_hours
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
    delivery_accepted = False
    try:
        if adapter is None:
            raise ChannelError(f"unknown companion channel: {channel_name}")
        try:
            message = await engine.checkin_message(checkin["user_id"])
        except asyncio.CancelledError:
            # No adapter has been invoked yet, so this retry cannot duplicate delivery.
            store.fail_checkin(checkin["id"], "generation cancelled before delivery")
            raise
        except Exception as exc:  # noqa: BLE001 - generation has no delivery side effect
            state = store.fail_checkin(checkin["id"], str(exc))
            log.warning("check-in %s generation failed (%s)", checkin["id"], exc)
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": state, "detail": str(exc)}
        profile = store.profile(checkin["user_id"])
        if (profile is None or not profile.checkins.enabled
                or profile.checkins.channel != channel_name
                or profile.checkins.address != checkin["address"]):
            store.cancel_claimed_checkin(checkin["id"])
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "cancelled", "detail": "check-in preferences changed before delivery"}
        if profile.checkins.quiet_hours and in_quiet_hours(datetime.now(timezone.utc).astimezone(profile.tz()), profile.checkins.quiet_hours):
            store.cancel_claimed_checkin(checkin["id"])
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "cancelled", "detail": "current time is inside quiet hours"}
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
        validate_delivery_address(store, checkin["user_id"], channel_name, address)
        # Only the exact built-in adapter on this SQLite store has no external side effect.
        # Other stores/adapters retain the explicit accepted/unknown delivery boundary.
        if (channel_name == "local" and type(adapter) is LocalChannel and adapter.store is store
                and isinstance(store, CompanionStore)):
            if not store.finish_local_checkin(checkin["id"], address, message):
                return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                        "delivered": False, "status": "cancelled", "detail": "delivery preferences changed at commit"}
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": True, "detail": "stored in local conversation"}
        try:
            result = await adapter.send(address, message)
        except asyncio.CancelledError:
            store.mark_checkin_unknown(checkin["id"], "delivery outcome unknown: send cancelled")
            raise
        except DeliveryOutcomeUnknown as exc:
            store.mark_checkin_unknown(checkin["id"], str(exc))
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "failed", "detail": str(exc)}
        except ChannelError:
            # Preserve the existing explicit adapter failure/retry contract.
            raise
        except Exception as exc:  # noqa: BLE001 - adapter may already have sent
            store.mark_checkin_unknown(checkin["id"], f"delivery outcome unknown: {exc}")
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "failed", "detail": f"delivery outcome unknown: {exc}"}
        if not result.delivered:
            raise ChannelError(result.detail or "channel did not deliver check-in")
        delivery_accepted = True
        try:
            store.finish_checkin(checkin["id"], message)
        except Exception as exc:  # noqa: BLE001 - publication may fail after accepted delivery
            detail = f"delivery accepted; completion publication outcome unknown: {exc}"
            # If this fallback write also fails, propagate. Never put accepted work back in the queue.
            store.mark_checkin_unknown(checkin["id"], detail)
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": True, "status": "failed", "detail": detail}
        return {
            "claimed": True,
            "checkin_id": checkin["id"],
            "user_id": checkin["user_id"],
            "delivered": result.delivered,
            "detail": result.detail,
        }
    except (ChannelError, ValueError, RuntimeError) as exc:
        if delivery_accepted:
            raise
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

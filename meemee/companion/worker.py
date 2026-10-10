from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from ..config import Settings
from .channels import ChannelError, DeliveryOutcomeUnknown, LocalChannel, validate_delivery_address
from .checkin_leases import LostCheckinClaim
from .checkins import CheckInScheduler, in_quiet_hours
from .engine import CompanionEngine
from .store import CompanionStore

log = logging.getLogger("meemee.companion.worker")


async def _deliver_claim(
    store: CompanionStore,
    engine: CompanionEngine,
    channels: dict,
    checkin: dict,
) -> dict:
    """Claim and deliver at most one due check-in. Returns a delivery summary."""
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
            store.fail_checkin_claim(checkin, "generation cancelled before delivery")
            raise
        except Exception as exc:  # noqa: BLE001 - generation has no delivery side effect
            state = store.fail_checkin_claim(checkin, str(exc))
            log.warning("check-in %s generation failed (%s)", checkin["id"], exc)
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": state, "detail": str(exc)}
        profile = store.profile(checkin["user_id"])
        if (profile is None or not profile.checkins.enabled
                or profile.checkins.channel != channel_name
                or profile.checkins.address != checkin["address"]):
            store.cancel_checkin_claim(checkin)
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "cancelled", "detail": "check-in preferences changed before delivery"}
        if profile.checkins.quiet_hours and in_quiet_hours(datetime.now(timezone.utc).astimezone(profile.tz()), profile.checkins.quiet_hours):
            store.cancel_checkin_claim(checkin)
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
        # Only the exact built-in adapter on supported stores has no external side effect.
        # Other adapters retain the explicit accepted/unknown delivery boundary.
        supported_local = type(store) is CompanionStore
        if (channel_name == "local" and type(adapter) is LocalChannel and adapter.store is store
                and not supported_local):
            from meemee_persist_pg.companion import CompanionStore as PGCompanionStore

            supported_local = type(store) is PGCompanionStore
        if (channel_name == "local" and type(adapter) is LocalChannel and adapter.store is store
                and supported_local):
            if not store.finish_local_checkin(checkin["id"], address, message, claim=checkin):
                return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                        "delivered": False, "status": "cancelled", "detail": "delivery preferences changed at commit"}
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": True, "detail": "stored in local conversation"}
        if not store.start_checkin_delivery(checkin):
            return {"claimed": True, "checkin_id": checkin["id"], "delivered": False,
                    "status": "cancelled", "detail": "delivery permission changed at intent"}
        try:
            result = await adapter.send(address, message)
        except asyncio.CancelledError:
            store.unknown_checkin_claim(checkin, "delivery outcome unknown: send cancelled")
            raise
        except DeliveryOutcomeUnknown as exc:
            store.unknown_checkin_claim(checkin, str(exc))
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "failed", "detail": str(exc)}
        except ChannelError:
            # Preserve the existing explicit adapter failure/retry contract.
            raise
        except Exception as exc:  # noqa: BLE001 - adapter may already have sent
            store.unknown_checkin_claim(checkin, f"delivery outcome unknown: {exc}")
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": False, "status": "failed", "detail": f"delivery outcome unknown: {exc}"}
        if not result.delivered:
            raise ChannelError(result.detail or "channel did not deliver check-in")
        delivery_accepted = True
        try:
            store.finish_checkin_claim(checkin, message)
        except Exception as exc:  # noqa: BLE001 - publication may fail after accepted delivery
            detail = f"delivery accepted; completion publication outcome unknown: {exc}"
            # If this fallback write also fails, propagate. Never put accepted work back in the queue.
            store.unknown_checkin_claim(checkin, detail)
            return {"claimed": True, "checkin_id": checkin["id"], "user_id": checkin["user_id"],
                    "delivered": True, "status": "failed", "detail": detail}
        return {
            "claimed": True,
            "checkin_id": checkin["id"],
            "user_id": checkin["user_id"],
            "delivered": result.delivered,
            "detail": result.detail,
        }
    except LostCheckinClaim:
        raise
    except (ChannelError, ValueError, RuntimeError) as exc:
        if delivery_accepted:
            raise
        state = store.fail_checkin_claim(checkin, str(exc), proven_not_delivered=isinstance(exc, ChannelError))
        log.warning("check-in %s delivery failed (%s)", checkin["id"], exc)
        return {
            "claimed": True,
            "checkin_id": checkin["id"],
            "user_id": checkin["user_id"],
            "delivered": False,
            "status": state,
            "detail": str(exc),
        }


async def deliver_due_once(store: CompanionStore, engine: CompanionEngine, channels: dict,
                           *, lease_seconds: int = 300) -> dict:
    """Recover expired work, acquire a fenced generation, keep its lease alive."""
    store.recover_checkin_claims()
    claim=store.claim_checkin_fenced(lease_seconds=lease_seconds)
    if claim is None:return {"claimed":False}
    task=asyncio.create_task(_deliver_claim(store,engine,channels,claim))
    async def heartbeat():
        while True:
            await asyncio.sleep(lease_seconds/3)
            store.renew_checkin_claim(claim,lease_seconds=lease_seconds)
    beat=asyncio.create_task(heartbeat())
    try:
        done,_=await asyncio.wait((task,beat),return_when=asyncio.FIRST_COMPLETED)
        if beat in done:
            # Any renewal error stops new actions; never infer send outcome.
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            beat.result()
        return await task
    except LostCheckinClaim:
        return {"claimed":True,"checkin_id":claim['id'],"delivered":False,
                "status":"claim_lost","detail":"generation no longer owns check-in; outcome may be unknown"}
    finally:
        task.cancel();beat.cancel()
        await asyncio.gather(task,beat,return_exceptions=True)


async def checkin_forever(settings: Settings | None = None) -> None:
    """Durable proactive check-in loop: plan slots, then deliver what is due."""
    settings = settings or Settings()
    from .runtime import build_companion_async

    companion = await build_companion_async(settings)
    async with AsyncExitStack() as cleanup:
        owned_persistence = getattr(companion, "owned_persistence", None)
        if owned_persistence is not None:
            cleanup.callback(owned_persistence.close)
        cleanup.push_async_callback(companion.model.aclose)
        for channel in companion.channels.values():
            close = getattr(channel, "aclose", None)
            if close is not None:
                cleanup.push_async_callback(close)
        scheduler = CheckInScheduler(companion.store)
        while True:
            scheduler.plan_all()
            summary = await deliver_due_once(companion.store, companion.engine, companion.channels)
            if not summary["claimed"]:
                await asyncio.sleep(settings.companion_checkin_poll_seconds)

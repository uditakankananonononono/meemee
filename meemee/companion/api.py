from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .channels import ChannelError, validate_delivery_address
from .checkins import CheckInScheduler
from .engine import CompanionEngine
from .models import ChatRequest, CheckInPreferences, FactInput, PersonaConfig, UserProfile
from .store import CompanionStore
from .worker import deliver_due_once


class WebhookControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=500)


class ProfileUpsert(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    timezone: str = "UTC"
    persona: PersonaConfig = PersonaConfig()
    checkins: CheckInPreferences = CheckInPreferences()


class PersonaUpdate(BaseModel):
    persona: PersonaConfig


class CheckInUpdate(BaseModel):
    checkins: CheckInPreferences


def build_companion_router(
    store: CompanionStore,
    engine: CompanionEngine,
    channels: dict,
    auth,
    audit=None,
) -> APIRouter:
    """Companion HTTP surface with dedicated companion:read/companion:write scopes."""
    router = APIRouter(prefix="/v1/companion", tags=["companion"])
    read = Depends(auth.dependency("companion:read"))
    write = Depends(auth.dependency("companion:write"))
    admin = Depends(auth.dependency("admin"))

    def audit_event(action: str, principal, resource: str, detail: dict) -> None:
        if audit is not None:
            audit.append(getattr(principal, "id", "unknown"), action, resource, "success", detail)

    def owned(principal, user_id: str) -> None:
        """Fail closed unless the authenticated principal owns this companion user.

        Every companion resource belongs to exactly one principal. Admin scope
        (operator console, bootstrap) may manage any user; any other principal
        may only address user_ids equal to its own principal id. Cross-owner
        access returns 404, the same as a nonexistent user, so a caller cannot
        enumerate which companion users exist.
        """
        principal_id = getattr(principal, "id", "")
        scopes = getattr(principal, "scopes", frozenset())
        if not principal_id:
            raise HTTPException(status_code=403, detail="unknown principal")
        if "admin" in scopes:
            return
        if principal_id != user_id:
            raise HTTPException(status_code=404, detail="unknown companion user")

    def destination_owner(principal, user_id):
        # Admin authority to manage profiles is not endpoint-control proof.
        if not getattr(principal, 'id', '') or principal.id != user_id:
            raise HTTPException(status_code=404, detail="unknown companion user")
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")

    @router.post("/users/{user_id}/destinations/webhook/verify", status_code=201)
    async def verify_destination(user_id: str, request: WebhookControlRequest, principal=write):
        from .destination_control import DestinationVerificationError, verify_webhook_control
        destination_owner(principal, user_id)
        try:
            grant = await verify_webhook_control(store, user_id, request.destination)
        except DestinationVerificationError:
            raise HTTPException(status_code=422, detail="destination verification refused") from None
        audit_event("companion.destination.verified", principal, user_id, {"grant_id": grant['id']})
        return grant

    @router.get("/users/{user_id}/destinations")
    def destinations(user_id: str, principal=read):
        destination_owner(principal, user_id)
        return {"grants": store.list_destination_grants(user_id),
                "unverifiable_channels": ["whatsapp", "imessage"]}

    @router.delete("/users/{user_id}/destinations/{grant_id}")
    def revoke_destination(user_id: str, grant_id: str, principal=write):
        destination_owner(principal, user_id)
        if not store.revoke_destination(user_id, grant_id):
            raise HTTPException(status_code=404, detail="unknown destination grant")
        audit_event("companion.destination.revoked", principal, user_id, {"grant_id": grant_id})
        return {"id": grant_id, "revoked": True}

    @router.get("/users")
    def list_users(limit: int = 100, principal=read):
        if "admin" not in getattr(principal, "scopes", frozenset()):
            raise HTTPException(status_code=403, detail="user inventory requires the admin scope")
        return {"users": store.list_users(limit)}

    @router.put("/users/{user_id}")
    def upsert_user(user_id: str, request: ProfileUpsert, principal=write):
        owned(principal, user_id)
        try:
            profile = UserProfile(
                user_id=user_id,
                display_name=request.display_name,
                timezone=request.timezone,
                persona=request.persona,
                checkins=request.checkins,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            validate_delivery_address(store, user_id, profile.checkins.channel, profile.checkins.address)
        except ChannelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = store.upsert_user(profile)
        if not profile.checkins.enabled:
            store.cancel_pending_checkins(user_id)
        audit_event("companion.user.upsert", principal, user_id, {})
        return saved

    @router.get("/users/{user_id}")
    def get_user(user_id: str, principal=read):
        owned(principal, user_id)
        record = store.get_user(user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        return record

    @router.put("/users/{user_id}/persona")
    def update_persona(user_id: str, request: PersonaUpdate, principal=write):
        owned(principal, user_id)
        profile = store.profile(user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        profile.persona = request.persona
        audit_event("companion.persona.update", principal, user_id, {})
        return store.upsert_user(profile)

    @router.put("/users/{user_id}/checkins")
    def update_checkins(user_id: str, request: CheckInUpdate, principal=write):
        owned(principal, user_id)
        profile = store.profile(user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        try:
            validate_delivery_address(store, user_id, request.checkins.channel, request.checkins.address)
        except ChannelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        profile.checkins = request.checkins
        updated = store.upsert_user(profile)
        if not request.checkins.enabled:
            cancelled = store.cancel_pending_checkins(user_id)
        else:
            cancelled = 0
        audit_event("companion.checkins.update", principal, user_id, {})
        return {"user": updated, "cancelled_pending": cancelled}

    @router.get("/users/{user_id}/facts")
    def list_facts(user_id: str, query: str | None = None, limit: int = 200, principal=read):
        owned(principal, user_id)
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        if query:
            return {"facts": store.search_facts(user_id, query, limit=min(limit, 50))}
        return {"facts": store.list_facts(user_id, limit=limit)}

    @router.post("/users/{user_id}/facts", status_code=201)
    def add_fact(user_id: str, fact: FactInput, principal=write):
        owned(principal, user_id)
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        created = store.add_fact(user_id, fact, source=f"api:{getattr(principal, 'id', 'unknown')}")
        audit_event("companion.fact.add", principal, user_id, {"fact_id": created["id"]})
        return created

    @router.delete("/users/{user_id}/facts/{fact_id}")
    def retire_fact(user_id: str, fact_id: int, principal=write):
        owned(principal, user_id)
        fact = store.get_fact(fact_id)
        if fact is None or fact["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="unknown fact for user")
        store.supersede_fact(fact_id)
        audit_event("companion.fact.retire", principal, user_id, {"fact_id": fact_id})
        return {"fact_id": fact_id, "active": False}

    @router.post("/chat")
    async def chat(request: ChatRequest, principal=write):
        owned(principal, request.user_id)
        try:
            return await engine.reply(
                request.user_id, request.text, request.channel, request.conversation_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/users/{user_id}/conversations")
    def list_conversations(user_id: str, limit: int = 50, principal=read):
        owned(principal, user_id)
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        return {"conversations": store.list_conversations(user_id, limit)}

    @router.get("/conversations/{conversation_id}/messages")
    def conversation_messages(conversation_id: str, limit: int = 100, principal=read):
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        owned(principal, conversation["user_id"])
        return {"messages": store.history(conversation_id, limit)}

    @router.post("/users/{user_id}/checkins/plan", status_code=201)
    def plan_checkin(user_id: str, principal=write):
        owned(principal, user_id)
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        row = CheckInScheduler(store).plan_user(user_id)
        if row is None:
            raise HTTPException(status_code=409, detail="check-ins are disabled for this user")
        return row

    @router.get("/users/{user_id}/checkins")
    def list_checkins(user_id: str, status: str | None = None, limit: int = 50, principal=read):
        owned(principal, user_id)
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        return {"checkins": store.list_checkins(user_id, status, limit)}

    @router.post("/checkins/tick")
    async def checkin_tick(principal=admin):
        planned = CheckInScheduler(store).plan_all()
        delivered = []
        while True:
            summary = await deliver_due_once(store, engine, channels)
            if not summary["claimed"]:
                break
            delivered.append(summary)
        return {"planned": len(planned), "deliveries": delivered}

    return router

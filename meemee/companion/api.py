from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .checkins import CheckInScheduler
from .engine import CompanionEngine
from .models import ChatRequest, CheckInPreferences, FactInput, PersonaConfig, UserProfile
from .store import CompanionStore
from .worker import deliver_due_once


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

    @router.get("/users")
    def list_users(limit: int = 100, principal=read):
        return {"users": store.list_users(limit)}

    @router.put("/users/{user_id}")
    def upsert_user(user_id: str, request: ProfileUpsert, principal=write):
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
        saved = store.upsert_user(profile)
        if not profile.checkins.enabled:
            store.cancel_pending_checkins(user_id)
        audit_event("companion.user.upsert", principal, user_id, {})
        return saved

    @router.get("/users/{user_id}")
    def get_user(user_id: str, principal=read):
        record = store.get_user(user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        return record

    @router.put("/users/{user_id}/persona")
    def update_persona(user_id: str, request: PersonaUpdate, principal=write):
        profile = store.profile(user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        profile.persona = request.persona
        audit_event("companion.persona.update", principal, user_id, {})
        return store.upsert_user(profile)

    @router.put("/users/{user_id}/checkins")
    def update_checkins(user_id: str, request: CheckInUpdate, principal=write):
        profile = store.profile(user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
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
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        if query:
            return {"facts": store.search_facts(user_id, query, limit=min(limit, 50))}
        return {"facts": store.list_facts(user_id, limit=limit)}

    @router.post("/users/{user_id}/facts", status_code=201)
    def add_fact(user_id: str, fact: FactInput, principal=write):
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        created = store.add_fact(user_id, fact, source=f"api:{getattr(principal, 'id', 'unknown')}")
        audit_event("companion.fact.add", principal, user_id, {"fact_id": created["id"]})
        return created

    @router.delete("/users/{user_id}/facts/{fact_id}")
    def retire_fact(user_id: str, fact_id: int, principal=write):
        fact = store.get_fact(fact_id)
        if fact is None or fact["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="unknown fact for user")
        store.supersede_fact(fact_id)
        audit_event("companion.fact.retire", principal, user_id, {"fact_id": fact_id})
        return {"fact_id": fact_id, "active": False}

    @router.post("/chat")
    async def chat(request: ChatRequest, principal=write):
        try:
            return await engine.reply(
                request.user_id, request.text, request.channel, request.conversation_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/users/{user_id}/conversations")
    def list_conversations(user_id: str, limit: int = 50, principal=read):
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        return {"conversations": store.list_conversations(user_id, limit)}

    @router.get("/conversations/{conversation_id}/messages")
    def conversation_messages(conversation_id: str, limit: int = 100, principal=read):
        if store.get_conversation(conversation_id) is None:
            raise HTTPException(status_code=404, detail="unknown conversation")
        return {"messages": store.history(conversation_id, limit)}

    @router.post("/users/{user_id}/checkins/plan", status_code=201)
    def plan_checkin(user_id: str, principal=write):
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="unknown companion user")
        row = CheckInScheduler(store).plan_user(user_id)
        if row is None:
            raise HTTPException(status_code=409, detail="check-ins are disabled for this user")
        return row

    @router.get("/users/{user_id}/checkins")
    def list_checkins(user_id: str, status: str | None = None, limit: int = 50, principal=read):
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

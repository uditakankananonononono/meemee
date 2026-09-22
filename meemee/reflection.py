from __future__ import annotations

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from .context import ContextStore
from .personal_model import PersonalItemInput, PersonalModelStore


class ReflectionModel(Protocol):
    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.1, max_tokens: int | None = None) -> str: ...


class ReflectionClaim(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=240)
    value: str = Field(min_length=1, max_length=10_000)
    confidence: float = Field(ge=0, le=1)
    source_id: str
    source_record_id: str
    valid_from: str | None = None
    valid_until: str | None = None


class ReflectionPayload(BaseModel):
    claims: list[ReflectionClaim] = Field(default_factory=list, max_length=20)


PROMPT = """Infer only durable personal facts directly supported by the supplied records.
Allowed kinds: goal, relationship, project, preference, routine, constraint.
Every claim must cite exactly one supplied source_id and source_record_id. Do not infer sensitive traits,
emotions, diagnoses, identity or intent. Return strict JSON {"claims": [...]} and no other text."""


class PersonalModelReflector:
    def __init__(self, context: ContextStore, personal: PersonalModelStore, model: ReflectionModel):
        self.context, self.personal, self.model = context, personal, model

    async def reflect(self, owner_id: str, limit: int = 50) -> dict:
        records = self.context.recent(owner_id, limit)
        if not records:
            return {"considered": 0, "accepted": 0, "rejected": 0, "items": []}
        supplied = {(row["source_id"], row["external_id"]) for row in records}
        compact = [{"source_id": row["source_id"], "source_record_id": row["external_id"], "kind": row["kind"],
                    "title": row["title"], "content": row["content"], "occurred_at": row["occurred_at"]} for row in records]
        raw = await self.model.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(compact)}], temperature=0.1, max_tokens=2500)
        try:
            payload = ReflectionPayload.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"reflection model returned invalid claims: {exc}") from exc
        accepted, rejected, items = 0, 0, []
        allowed = {"goal", "relationship", "project", "preference", "routine", "constraint"}
        for claim in payload.claims:
            if claim.kind not in allowed or (claim.source_id, claim.source_record_id) not in supplied:
                rejected += 1
                continue
            evidence_hash = hashlib.sha256(f"{claim.source_id}\0{claim.source_record_id}".encode()).hexdigest()
            item = self.personal.upsert(owner_id, PersonalItemInput(
                kind=claim.kind, title=claim.title, value=claim.value, confidence=claim.confidence,
                source_id=claim.source_id, source_record_id=claim.source_record_id,
                valid_from=claim.valid_from, valid_until=claim.valid_until,
            ))
            item["evidence_hash"] = evidence_hash
            items.append(item); accepted += 1
        return {"considered": len(records), "accepted": accepted, "rejected": rejected, "items": items}

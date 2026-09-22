from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from ..context import ContextStore
from ..sensitive import scrub_text
from .models import ChatReply, FactInput, UserProfile
from .store import CompanionStore

log = logging.getLogger("meemee.companion")

SYSTEM_TEMPLATE = """You are {persona_name}, the personal companion of {user_name}.
Persona: speak in a tone that is {tone}. Language: {language}. Emoji: {emoji}.
{style_block}{custom_block}
You have persistent memory of this user. Known facts:
{facts_block}
Rules: be honest about what you know and do not know; never invent facts about the user;
keep replies conversational, not bureaucratic; do not repeat the user's message back at them.
If a stored fact looks stale or contradicted, trust the newest user message."""

EXTRACTION_PROMPT = """Extract durable facts worth remembering about the user from this exchange.
Only long-lived facts (preferences, goals, people, projects, constraints); skip small talk.
Respond as strict JSON: {{"facts": [{{"category": "...", "text": "..."}}]}} with at most {limit} items,
or {{"facts": []}} when nothing is durable.

User said: {user_text}
Assistant replied: {assistant_text}"""

CHECKIN_TEMPLATE = """Write one short proactive check-in message for {user_name}. It is {local_time} their time.
Use the known facts when they make the message specific; never list the facts back.
Ask at most one question. Do not ask for tasks or pitch features. Keep it under 80 words."""


class ChatModel(Protocol):
    async def chat(
        self, messages: list[dict[str, str]], temperature: float = 0.7, max_tokens: int | None = None
    ) -> str: ...


class CompanionEngine:
    """Persona-conditioned conversational engine over durable per-user memory."""

    def __init__(
        self,
        model: ChatModel,
        store: CompanionStore,
        history_limit: int = 40,
        fact_limit: int = 12,
        extract_limit: int = 5,
        temperature: float = 0.7,
        context: ContextStore | None = None,
    ):
        self.model = model
        self.store = store
        self.history_limit = history_limit
        self.fact_limit = fact_limit
        self.extract_limit = extract_limit
        self.temperature = temperature
        self.context = context

    def ensure_profile(self, user_id: str, display_name: str | None = None) -> UserProfile:
        profile = self.store.profile(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id, display_name=display_name or user_id)
            self.store.upsert_user(profile)
        return profile

    def _system_prompt(self, profile: UserProfile, facts: list[dict[str, Any]]) -> str:
        persona = profile.persona
        style_block = ""
        if persona.style_rules:
            rules = "\n".join(f"- {rule}" for rule in persona.style_rules)
            style_block = f"Style rules:\n{rules}\n"
        custom_block = ""
        if persona.custom_instructions.strip():
            custom_block = f"Custom instructions: {persona.custom_instructions.strip()}\n"
        facts_block = "\n".join(
            f"- [{fact['category']}] {fact['text']}" for fact in facts
        ) or "- none recorded yet"
        return SYSTEM_TEMPLATE.format(
            persona_name=persona.display_name,
            user_name=profile.display_name,
            tone=persona.tone,
            language=persona.language,
            emoji="yes, sparingly" if persona.use_emoji else "no",
            style_block=style_block,
            custom_block=custom_block,
            facts_block=facts_block,
        )

    def _recall(self, user_id: str, text: str) -> list[dict[str, Any]]:
        matched = self.store.search_facts(user_id, text, limit=self.fact_limit)
        if len(matched) >= self.fact_limit:
            return matched
        seen = {fact["id"] for fact in matched}
        for fact in self.store.list_facts(user_id, limit=self.fact_limit * 3):
            if fact["id"] not in seen:
                matched.append(fact)
                seen.add(fact["id"])
            if len(matched) >= self.fact_limit:
                break
        return matched

    async def reply(
        self,
        user_id: str,
        text: str,
        channel: str = "local",
        conversation_id: str | None = None,
    ) -> ChatReply:
        profile = self.ensure_profile(user_id)
        conversation = self._conversation(profile.user_id, channel, conversation_id)
        self.store.add_message(conversation["id"], "user", text)
        facts = self._recall(profile.user_id, text)
        context = self.context.assemble(profile.user_id, text, self.fact_limit) if self.context else {"records": []}
        history = self.store.history(conversation["id"], limit=self.history_limit)
        system = self._system_prompt(profile, facts)
        if context["records"]:
            grounded = "\n".join(f"- [{row['source_id']}] {row['title']}: {row['content']} (provenance: {json.dumps(row['provenance'], sort_keys=True)})" for row in context["records"])
            system += "\nUnified personal context from permitted sources:\n" + grounded
        messages = [{"role": "system", "content": system}]
        messages.extend(
            {"role": row["role"], "content": row["content"]} for row in history
        )
        reply_text = await self.model.chat(messages, temperature=self.temperature)
        self.store.add_message(conversation["id"], "assistant", reply_text)
        learned = await self._extract_facts(profile.user_id, text, reply_text, conversation["id"])
        return ChatReply(
            conversation_id=conversation["id"],
            reply=reply_text,
            facts_learned=learned,
            persona=profile.persona.display_name,
        )

    def _conversation(
        self, user_id: str, channel: str, conversation_id: str | None
    ) -> dict[str, Any]:
        if conversation_id:
            existing = self.store.get_conversation(conversation_id)
            if existing is None or existing["user_id"] != user_id:
                raise ValueError(f"unknown conversation for user {user_id}: {conversation_id}")
            return existing
        latest = self.store.latest_conversation(user_id, channel)
        if latest is not None:
            return latest
        return self.store.start_conversation(user_id, channel)

    async def _extract_facts(
        self, user_id: str, user_text: str, assistant_text: str, conversation_id: str
    ) -> int:
        prompt = EXTRACTION_PROMPT.format(
            limit=self.extract_limit,
            user_text=scrub_text(user_text)[:2000],
            assistant_text=scrub_text(assistant_text)[:2000],
        )
        try:
            raw = await self.model.chat(
                [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=600
            )
            payload = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            candidates = payload.get("facts", [])[: self.extract_limit]
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("fact extraction skipped: %s", exc)
            return 0
        learned = 0
        for candidate in candidates:
            try:
                fact = FactInput(**candidate)
            except (TypeError, ValueError):
                continue
            if self.store.find_fact_text(user_id, fact.text) is not None:
                continue
            self.store.add_fact(user_id, fact, source=f"conversation:{conversation_id}")
            learned += 1
        return learned

    async def checkin_message(self, user_id: str) -> str:
        profile = self.store.profile(user_id)
        if profile is None:
            raise ValueError(f"unknown companion user: {user_id}")
        facts = self.store.list_facts(user_id, limit=self.fact_limit)
        from datetime import datetime

        local_time = datetime.now(profile.tz()).strftime("%A %H:%M")
        messages = [
            {"role": "system", "content": self._system_prompt(profile, facts)},
            {"role": "user", "content": CHECKIN_TEMPLATE.format(
                user_name=profile.display_name, local_time=local_time
            )},
        ]
        return await self.model.chat(messages, temperature=self.temperature, max_tokens=220)

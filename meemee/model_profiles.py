"""Named model profiles and role-based routing with ordered fallback.

Free-first: the default route is the local OpenAI-compatible endpoint (Ollama).
Self-hosted open-weight profiles (Inkling, Inkling-Small via vLLM/SGLang) are
free to use but need hardware the operator provides. Paid hosted profiles
(Sakana Fugu) are never selected unless an API key is configured AND
``allow_paid_models`` is enabled.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from .llm import ModelError, OpenAICompatibleModel
from .types import AgentDecision

ProfileKind = Literal["local", "self_hosted", "hosted_paid", "hosted_free"]
ROLES = ("agent", "chat", "reflection")
_last_trace: ContextVar[dict[str, Any] | None] = ContextVar("meemee_model_trace", default=None)


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    kind: ProfileKind
    api_key: str | None = None
    requires_key: bool = False
    description: str = ""
    source_url: str = ""

    @property
    def paid(self) -> bool:
        return self.kind == "hosted_paid"

    def unavailable_reason(self, allow_paid: bool) -> str | None:
        if self.requires_key and not self.api_key:
            return "no API key configured"
        if self.paid and not allow_paid:
            return "paid profile; set MEEMEE_ALLOW_PAID_MODELS=true to enable"
        return None

    def public_dict(self, allow_paid: bool) -> dict[str, Any]:
        reason = self.unavailable_reason(allow_paid)
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "kind": self.kind,
            "key_configured": bool(self.api_key),
            "available": reason is None,
            "unavailable_reason": reason,
            "description": self.description,
            "source_url": self.source_url,
        }


def builtin_profiles(settings: Any) -> dict[str, ModelProfile]:
    """Profiles shipped with Meemee. Only ``local`` is usable with zero setup."""
    profiles = [
        ModelProfile(
            name="local",
            base_url=settings.model_base_url,
            model=settings.model_name,
            kind="local",
            api_key=settings.model_api_key or "local",
            description="Default local OpenAI-compatible endpoint (Ollama by default).",
        ),
        ModelProfile(
            name="inkling",
            base_url=settings.hf_router_base_url,
            model=settings.hf_inkling_model,
            kind="hosted_free",
            api_key=settings.hf_token,
            requires_key=True,
            description=(
                "Thinking Machines Lab Inkling-Small (Apache-2.0 open weights, 276B MoE / 12B active) "
                "through the Hugging Face Inference Providers router. Needs a free HF token; runs on "
                "HF's monthly free credits, then prepaid credits (no surprise billing)."
            ),
            source_url="https://huggingface.co/thinkingmachines/Inkling-Small",
        ),
        ModelProfile(
            name="inkling-large",
            base_url=settings.hf_router_base_url,
            model="thinkingmachines/Inkling",
            kind="hosted_free",
            api_key=settings.hf_token,
            requires_key=True,
            description=(
                "Full Inkling (975B MoE / 41B active) through the HF router. Same token; costs about "
                "4x more per token than Inkling-Small, so free credits go faster."
            ),
            source_url="https://huggingface.co/thinkingmachines/Inkling",
        ),
        ModelProfile(
            name="inkling-vllm",
            base_url=settings.inkling_base_url,
            model=settings.inkling_self_hosted_model,
            kind="self_hosted",
            api_key=settings.inkling_api_key or "local",
            description=(
                "Self-hosted Inkling weights served by vLLM or SGLang at MEEMEE_INKLING_BASE_URL. "
                "Free weights, but needs GPU server hardware."
            ),
            source_url="https://huggingface.co/thinkingmachines/Inkling-Small",
        ),
        ModelProfile(
            name="fugu",
            base_url="https://api.sakana.ai/v1",
            model=settings.fugu_model,
            kind="hosted_paid",
            api_key=settings.fugu_api_key,
            requires_key=True,
            description=(
                "Sakana AI Fugu, a closed multi-model orchestrator sold through the paid Sakana API "
                "(OpenAI-compatible). Not open weights. Optional only."
            ),
            source_url="https://github.com/SakanaAI/fugu",
        ),
    ]
    return {p.name: p for p in profiles}


def _custom_profiles(raw: str | None) -> dict[str, ModelProfile]:
    if not raw:
        return {}
    text = raw
    candidate = Path(raw).expanduser()
    if not raw.lstrip().startswith("[") and candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
    try:
        items = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model profiles must be a JSON list or a path to one: {exc}") from exc
    if not isinstance(items, list):
        raise TypeError("model profiles must be a JSON list")
    out: dict[str, ModelProfile] = {}
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("each model profile must be an object")
        missing = [k for k in ("name", "base_url", "model") if not item.get(k)]
        if missing:
            raise ValueError(f"model profile missing fields: {', '.join(missing)}")
        kind = item.get("kind", "self_hosted")
        if kind not in ("local", "self_hosted", "hosted_paid", "hosted_free"):
            raise ValueError(f"model profile {item['name']!r} has unknown kind {kind!r}")
        key = item.get("api_key")
        key_env = item.get("api_key_env")
        if key_env:
            key = os.environ.get(key_env) or key
        out[item["name"]] = ModelProfile(
            name=item["name"],
            base_url=str(item["base_url"]).rstrip("/"),
            model=item["model"],
            kind=kind,
            api_key=key,
            requires_key=bool(item.get("requires_key", kind.startswith("hosted"))),
            description=item.get("description", ""),
            source_url=item.get("source_url", ""),
        )
    return out


def parse_routes(raw: str | None) -> dict[str, list[str]]:
    """Parse ``agent=local,inkling;chat=inkling-small,local`` into ordered lists."""
    routes = {role: ["local"] for role in ROLES}
    if not raw:
        return routes
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad route {part!r}; expected role=profile[,profile]")
        role, names = (s.strip() for s in part.split("=", 1))
        if role not in ROLES:
            raise ValueError(f"unknown route role {role!r}; roles are {', '.join(ROLES)}")
        chain = [n.strip() for n in names.split(",") if n.strip()]
        if not chain:
            raise ValueError(f"route {role!r} has no profiles")
        routes[role] = chain
    return routes


@dataclass
class ModelCatalog:
    profiles: dict[str, ModelProfile]
    routes: dict[str, list[str]]
    allow_paid: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> ModelCatalog:
        profiles = builtin_profiles(settings)
        profiles.update(_custom_profiles(settings.model_profiles))
        routes = parse_routes(settings.model_routes)
        for role, chain in routes.items():
            unknown = [n for n in chain if n not in profiles]
            if unknown:
                raise ValueError(f"route {role!r} references unknown profiles: {', '.join(unknown)}")
        return cls(profiles, routes, bool(settings.allow_paid_models))

    def chain(self, role: str) -> tuple[list[ModelProfile], dict[str, str]]:
        usable: list[ModelProfile] = []
        skipped: dict[str, str] = {}
        for name in self.routes[role]:
            profile = self.profiles[name]
            reason = profile.unavailable_reason(self.allow_paid)
            if reason:
                skipped[name] = reason
            else:
                usable.append(profile)
        return usable, skipped


@dataclass
class RoutedModel:
    """Implements the Model protocol (decide) plus chat() over an ordered fallback chain."""

    catalog: ModelCatalog
    role: str = "agent"
    timeout: float = 60.0
    max_attempts: int = 3
    client: httpx.AsyncClient | None = None
    last_profile: str | None = None
    attempts: list[dict[str, str]] = field(default_factory=list)
    _clients: dict[str, OpenAICompatibleModel] = field(default_factory=dict)

    def _model_for(self, profile: ModelProfile) -> OpenAICompatibleModel:
        if profile.name not in self._clients:
            self._clients[profile.name] = OpenAICompatibleModel(
                profile.base_url,
                profile.model,
                profile.api_key or "local",
                self.timeout,
                self.max_attempts,
                client=self.client,
            )
        return self._clients[profile.name]

    async def _run(self, op: str, *args: Any, **kwargs: Any) -> Any:
        usable, skipped = self.catalog.chain(self.role)
        attempts = [{"profile": n, "outcome": f"skipped: {r}"} for n, r in skipped.items()]
        self.attempts = attempts
        trace: dict[str, Any] = {"role": self.role, "profile": None, "model": None, "attempts": attempts}
        _last_trace.set(trace)
        if not usable:
            raise ModelError(f"no usable model profile for role {self.role!r}: {skipped}")
        errors: list[str] = []
        for profile in usable:
            try:
                result = await getattr(self._model_for(profile), op)(*args, **kwargs)
            except ModelError as exc:
                errors.append(f"{profile.name}: {exc}")
                attempts.append({"profile": profile.name, "outcome": f"failed: {str(exc)[:300]}"})
                continue
            self.last_profile = profile.name
            attempts.append({"profile": profile.name, "outcome": "ok"})
            trace.update(profile=profile.name, model=profile.model)
            return result
        raise ModelError(f"all model profiles failed for role {self.role!r}: " + " | ".join(errors))

    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        return await self._run("decide", messages)

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.7, max_tokens: int | None = None) -> str:
        return await self._run("chat", messages, temperature=temperature, max_tokens=max_tokens)

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            return
        for model in self._clients.values():
            await model.aclose()


async def probe_profile(profile: ModelProfile, timeout: float = 5.0, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Check an endpoint's /models listing; never sends a completion or spends tokens."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await client.get(
            f"{profile.base_url}/models",
            headers={"Authorization": f"Bearer {profile.api_key or 'local'}"},
        )
        if response.status_code >= 400:
            return {"name": profile.name, "reachable": False, "error": f"HTTP {response.status_code}"}
        data = response.json().get("data", [])
        ids = [m.get("id") for m in data if isinstance(m, dict)]
        return {"name": profile.name, "reachable": True, "model_listed": profile.model in ids, "served": ids[:20]}
    except (httpx.HTTPError, ValueError) as exc:
        return {"name": profile.name, "reachable": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}
    finally:
        if owns:
            await client.aclose()


def build_role_model(settings: Any, role: str) -> OpenAICompatibleModel | RoutedModel:
    """Model for one role: routed when profiles/routes are configured, else the plain local client."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if settings.model_routes or settings.model_profiles:
        return RoutedModel(
            ModelCatalog.from_settings(settings), role, settings.request_timeout, settings.model_max_attempts
        )
    return OpenAICompatibleModel(
        settings.model_base_url,
        settings.model_name,
        settings.model_api_key,
        settings.request_timeout,
        settings.model_max_attempts,
    )


def last_model_trace(model: Any) -> dict[str, Any]:
    """Which profile and model answered the most recent call made in this task.

    Routed models record a per-task trace (safe under concurrent requests). A plain client
    has no routing, so it reports its configured model as the single ``default`` profile.
    """
    if isinstance(model, RoutedModel):
        trace = _last_trace.get()
        return dict(trace) if trace else {"role": model.role, "profile": None, "model": None, "attempts": []}
    name = getattr(model, "model", None)
    return {"role": None, "profile": "default", "model": name, "attempts": [{"profile": "default", "outcome": "ok"}]}

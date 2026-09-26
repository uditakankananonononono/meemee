"""Meemee's connection to the shared model layer (instinct_models, vendored and pinned).

The same package is used by Meemee, Atlas and Sugarcode (repo uditakankananonononono/shared-models).
It is vendored under ``meemee/_vendor/instinct_models`` at the commit in
``meemee/_vendor/INSTINCT_MODELS_PIN``; refresh it with ``scripts/sync_instinct_models.sh <commit>``.

What Meemee gets from it:
- a ``shared`` model profile (``transport: "instinct"``) that routes Needle -> Ornith -> Inkling;
- per-product Needle LoRA training on Meemee's own owner-confirmed examples only;
- a read-only AI Library (theailibrary.co) catalog for browsing AI tools and prompts;
- an optional Jev evaluation client (TypeSafe AI's hosted System One model) via ``build_jev`` -
  key-gated and paid, so it is OFF unless MEEMEE_JEV_API_KEY or JEV_API_KEY is set.

Meemee handles personal data, so shared-layer tasks are private by default: they never go to
the hosted Hugging Face router unless ``MEEMEE_SHARED_ALLOW_HOSTED=true``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._vendor.instinct_models import (
    InklingHFRouter,
    InklingLocal,
    JevEval,
    NeedleLocal,
    OrnithOpenAICompat,
    Provider,
    Router,
    Task,
)
from ._vendor.instinct_models.training import (
    ExampleRow,
    NeedleLoRAJob,
    build_needle_jsonl,
    train_needle_lora,
)
from .llm import ModelError
from .types import AgentDecision

PRODUCT = "meemee"
PIN_FILE = Path(__file__).parent / "_vendor" / "INSTINCT_MODELS_PIN"


def vendor_pin() -> dict[str, str]:
    """The shared-models commit Meemee is pinned to."""
    out: dict[str, str] = {}
    for line in PIN_FILE.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def build_chain(settings: Any) -> list[Provider]:
    """Needle (on-device, tool turns only) -> Ornith local -> Inkling local -> HF router (opt-in)."""
    chain: list[Provider] = [
        NeedleLocal(settings.shared_needle_weights or None),
        OrnithOpenAICompat(settings.shared_ornith_url, settings.shared_ornith_model),
        InklingLocal(settings.shared_inkling_url, settings.shared_inkling_model),
    ]
    if settings.shared_allow_hosted:
        chain.append(InklingHFRouter(settings.hf_inkling_model, token=settings.hf_token or ""))
    return chain


def build_jev(env: dict | None = None) -> JevEval:
    """Jev evaluation client (TypeSafe AI's System One model, https://thejevai.com).

    Key from MEEMEE_JEV_API_KEY, else JEV_API_KEY; without one the client is unavailable and
    OFF - nothing is called or billed. Jev is hosted and paid (credits). It evaluates typed
    questions (choice / score / noul); it does not chat, and it must never receive private
    state, so nothing in the agent loop calls it automatically.
    """
    import os

    e = os.environ if env is None else env
    return JevEval(api_key=e.get("MEEMEE_JEV_API_KEY") or e.get("JEV_API_KEY"))


def generation_ready(settings: Any) -> str | None:
    """Why the shared profile cannot answer free-text/JSON turns, or None when it can."""
    gens = [p for p in build_chain(settings) if not isinstance(p, NeedleLocal)]
    if any(p.available() for p in gens):
        return None
    return ("no shared-layer generation model configured; set MEEMEE_SHARED_ORNITH_URL + "
            "MEEMEE_SHARED_ORNITH_MODEL, or MEEMEE_SHARED_INKLING_URL (or allow the metered HF router "
            "with MEEMEE_SHARED_ALLOW_HOSTED=true and MEEMEE_HF_TOKEN)")


def _extract_json(text: str) -> str:
    from .providers import extract_json_object

    return extract_json_object(text)


@dataclass
class SharedLayerModel:
    """Meemee Model protocol (decide/chat/aclose) on top of the shared instinct_models router."""

    settings: Any
    router: Router | None = None
    last_attempts: list[dict[str, str]] = field(default_factory=list)
    last_provider: str | None = None

    def __post_init__(self) -> None:
        if self.router is None:
            self.router = Router(build_chain(self.settings))

    async def _run(self, messages: list[dict[str, str]], max_tokens: int | None) -> str:
        task = Task(list(messages), tools=None, private=not self.settings.shared_allow_hosted,
                    max_tokens=max_tokens or 1024)
        assert self.router is not None
        routed = await asyncio.to_thread(self.router.run, task)
        self.last_attempts = [{"profile": a.provider, "outcome": a.outcome, "detail": a.detail} for a in routed.attempts]
        if not routed.ok or routed.result is None:
            summary = "; ".join(f"{a.provider}: {a.outcome}" + (f" ({a.detail})" if a.detail else "") for a in routed.attempts)
            raise ModelError(f"shared model layer produced no answer: {summary}")
        self.last_provider = routed.result.provider
        text = (routed.result.text or "").strip()
        if not text:
            raise ModelError(f"{routed.result.provider} returned an empty completion")
        return text

    async def decide(self, messages: list[dict[str, str]]) -> AgentDecision:
        text = await self._run(messages, None)
        try:
            return AgentDecision.model_validate_json(_extract_json(text))
        except ValueError as exc:
            raise ModelError(f"shared model returned an invalid decision: {exc}") from exc

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.7, max_tokens: int | None = None) -> str:
        return await self._run(messages, max_tokens)

    async def aclose(self) -> None:
        return None


class MeemeeDataset:
    """Meemee's DomainDataset: owner-confirmed examples from a JSONL file, tagged product="meemee".

    Each line: {"query", "tools", "answers", "confirmed", "source_ref", optional "reasoning",
    "system", "private", "product"}. Lines tagged with another product are dropped by the shared
    pipeline, and unconfirmed lines are dropped too, so the adapter trains on Meemee data only.
    """

    product = PRODUCT

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def rows(self) -> Iterable[ExampleRow]:
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            yield ExampleRow(
                query=rec["query"], tools=rec.get("tools", []), answers=rec.get("answers", []),
                confirmed=bool(rec.get("confirmed", False)), source_ref=rec.get("source_ref", f"{self.path.name}:{n}"),
                private=bool(rec.get("private", True)), reasoning=rec.get("reasoning"), system=rec.get("system"),
                product=rec.get("product", PRODUCT),
            )


def train_meemee_needle(examples: Path, out_dir: Path, *, epochs: int = 10, runner: Any = None) -> dict[str, Any]:
    """Build the Meemee Needle dataset and run the shared LoRA pipeline. Returns manifest + registry record."""
    out_dir = Path(out_dir)
    manifest = build_needle_jsonl(MeemeeDataset(examples), out_dir / "meemee-needle.jsonl")
    job = NeedleLoRAJob(PRODUCT, manifest["path"], str(out_dir), epochs=epochs)
    record = train_needle_lora(job) if runner is None else train_needle_lora(job, runner=runner)
    return {"manifest": manifest, "record": record}
